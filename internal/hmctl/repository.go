package hmctl

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"go.yaml.in/yaml/v3"
)

const secretPath = "conf/xray/credentials-secret.sops.yaml"

type repository struct {
	path        string
	root        *os.Root
	ctx         context.Context
	keyFile     string
	execCommand func(context.Context, string, []byte, string, ...string) ([]byte, error)
}

func command(ctx context.Context, dir string, input []byte, name string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Dir = dir
	cmd.Stdin = bytes.NewReader(input)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &bytes.Buffer{}
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("%s operation failed; check tool installation, authentication and repository state", name)
	}
	return out.Bytes(), nil
}
func openRepository(ctx context.Context, path string) (*repository, error) {
	if path == "" {
		out, err := command(ctx, "", nil, "git", "rev-parse", "--show-toplevel")
		if err != nil {
			return nil, errors.New("run inside the GitOps checkout or provide --repo")
		}
		path = strings.TrimSpace(string(out))
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, errors.New("invalid repository path")
	}
	absolute, err = filepath.EvalSymlinks(absolute)
	if err != nil {
		return nil, errors.New("repository does not exist")
	}
	root, err := os.OpenRoot(absolute)
	if err != nil {
		return nil, errors.New("cannot open repository")
	}
	if _, err := root.Stat(secretPath); err != nil {
		root.Close()
		return nil, errors.New("repository has no canonical Xray Secret")
	}
	return &repository{path: absolute, root: root, ctx: ctx}, nil
}
func (r *repository) close() { r.root.Close() }
func (r *repository) run(name string, args ...string) ([]byte, error) {
	return r.execute(nil, name, args...)
}
func (r *repository) lock() (func(), error) {
	lockRoot, path := r.root, ".hmctl.lock"
	info, statErr := r.root.Stat(".git")
	if statErr == nil && info.IsDir() {
		path = ".git/hmctl.lock"
	} else if statErr == nil {
		raw, err := r.run("git", "rev-parse", "--absolute-git-dir")
		if err != nil {
			return nil, err
		}
		metadata, err := os.OpenRoot(strings.TrimSpace(string(raw)))
		if err != nil {
			return nil, errors.New("cannot open Git metadata directory for locking")
		}
		lockRoot = metadata
		path = "hmctl.lock"
	} else if !os.IsNotExist(statErr) {
		return nil, errors.New("cannot inspect repository metadata")
	}
	f, err := lockRoot.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		if lockRoot != r.root {
			lockRoot.Close()
		}
		return nil, errors.New("repository is locked; another hmctl may be running (review a stale hmctl.lock before removing it)")
	}
	fmt.Fprintln(f, os.Getpid())
	f.Close()
	return func() {
		_ = lockRoot.Remove(path)
		if lockRoot != r.root {
			lockRoot.Close()
		}
	}, nil
}

func (r *repository) readYAML(path string, v any) error {
	data, err := r.root.ReadFile(path)
	if err != nil {
		return errors.New("cannot read repository configuration")
	}
	if err := yaml.Unmarshal(data, v); err != nil {
		return errors.New("invalid repository YAML configuration")
	}
	return nil
}
func (r *repository) atomicWrite(path string, data []byte) error {
	if r.ctx.Err() != nil {
		return errors.New("operation cancelled before writing configuration")
	}
	info, err := r.root.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return errors.New("managed file must be an existing regular file")
	}
	var b [12]byte
	if _, err = rand.Read(b[:]); err != nil {
		return err
	}
	temp := path + "." + hex.EncodeToString(b[:]) + ".tmp"
	f, err := r.root.OpenFile(temp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("cannot create encrypted/configuration staging file")
	}
	defer r.root.Remove(temp)
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	if err == nil {
		err = f.Chmod(info.Mode().Perm())
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.New("cannot stage configuration update")
	}
	if err = r.root.Rename(temp, path); err != nil {
		return errors.New("cannot atomically replace configuration")
	}
	return nil
}
func (r *repository) preparePR() error {
	status, err := r.run("git", "status", "--porcelain=v1", "-z")
	if err != nil {
		return err
	}
	branch, err := r.run("git", "symbolic-ref", "--short", "HEAD")
	if err != nil {
		return err
	}
	if len(status) > 0 || strings.TrimSpace(string(branch)) != "main" {
		return errors.New("start from a clean main checkout; existing work will not be committed")
	}
	if _, err = r.run("but", "pull", "--check"); err != nil {
		return err
	}
	local, err := r.run("git", "rev-parse", "HEAD")
	if err != nil {
		return err
	}
	remote, err := r.run("git", "rev-parse", "origin/main")
	if err != nil {
		return err
	}
	if !bytes.Equal(local, remote) {
		return errors.New("main differs from origin/main; run but pull and retry")
	}
	return nil
}
func changedPaths(status []byte) ([]string, error) {
	var paths []string
	for _, record := range bytes.Split(status, []byte{0}) {
		if len(record) == 0 {
			continue
		}
		if len(record) < 4 || (string(record[:2]) != " M" && string(record[:2]) != "M ") {
			return nil, errors.New("unexpected staged, renamed or untracked files; no commit made")
		}
		paths = append(paths, string(record[3:]))
	}
	sort.Strings(paths)
	return paths, nil
}

var prURL = regexp.MustCompile(`https://github\.com/[^\s<>]+/pull/[0-9]+`)

func (r *repository) openPR(paths []string, title, body string) (string, error) {
	raw, err := r.run("git", "status", "--porcelain=v1", "-z")
	if err != nil {
		return "", err
	}
	actual, err := changedPaths(raw)
	if err != nil {
		return "", err
	}
	expected := append([]string(nil), paths...)
	sort.Strings(expected)
	if strings.Join(actual, "\x00") != strings.Join(expected, "\x00") {
		return "", errors.New("unexpected working-tree changes; prepared files preserved, no commit made")
	}
	var b [6]byte
	if _, err = rand.Read(b[:]); err != nil {
		return "", err
	}
	branch := "codex/hmctl-" + hex.EncodeToString(b[:])
	if _, err = r.run("but", "commit", "-b", branch, "-m", title); err != nil {
		return "", fmt.Errorf("commit failed; prepared changes preserved: %w", err)
	}
	out, err := r.execute([]byte(title+"\n\n"+body), "but", "pr", "new", branch, "-F", "-")
	if err != nil {
		return "", fmt.Errorf("PR failed; encrypted/configuration commit preserved; retry but pr new %s -t", branch)
	}
	found := prURL.Find(out)
	if len(found) == 0 {
		return "", errors.New("PR command completed; inspect but status for its URL")
	}
	return string(found), nil
}

func (r *repository) execute(input []byte, name string, args ...string) ([]byte, error) {
	if r.execCommand != nil {
		return r.execCommand(r.ctx, r.path, input, name, args...)
	}
	return command(r.ctx, r.path, input, name, args...)
}
