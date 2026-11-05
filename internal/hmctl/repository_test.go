package hmctl

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestPRBoundaryRejectsDirtyStaleOrUnrelatedChanges(t *testing.T) {
	for _, scenario := range []string{"dirty", "wrong-branch", "stale"} {
		var calls [][]string
		r := &repository{ctx: context.Background()}
		r.execCommand = func(_ context.Context, _ string, _ []byte, name string, args ...string) ([]byte, error) {
			calls = append(calls, append([]string{name}, args...))
			if name == "git" && args[0] == "status" {
				if scenario == "dirty" {
					return []byte(" M README.md\x00"), nil
				}
				return nil, nil
			}
			if name == "git" && args[0] == "symbolic-ref" {
				if scenario == "wrong-branch" {
					return []byte("feature\n"), nil
				}
				return []byte("main\n"), nil
			}
			if name == "git" && args[0] == "rev-parse" {
				if scenario == "stale" && args[1] == "origin/main" {
					return []byte("new\n"), nil
				}
				return []byte("old\n"), nil
			}
			return nil, nil
		}
		if err := r.preparePR(); err == nil {
			t.Fatal("unsafe checkout accepted")
		}
		for _, call := range calls {
			if strings.Contains(strings.Join(call, " "), "commit") {
				t.Fatal("committed an unsafe checkout")
			}
		}
	}
	r := &repository{ctx: context.Background(), execCommand: func(context.Context, string, []byte, string, ...string) ([]byte, error) {
		return []byte(" M README.md\x00"), nil
	}}
	if _, err := r.openPR([]string{secretPath}, "Add VPN client", "body"); err == nil {
		t.Fatal("unrelated change committed")
	}
}
func TestPRMetadataNeverContainsAccountIdentityAndFailurePreservesCommit(t *testing.T) {
	for _, fail := range []bool{false, true} {
		commits := 0
		r := &repository{ctx: context.Background()}
		r.execCommand = func(_ context.Context, _ string, input []byte, name string, args ...string) ([]byte, error) {
			if name == "git" {
				return []byte(" M " + secretPath + "\x00"), nil
			}
			if args[0] == "commit" {
				commits++
				if args[len(args)-1] != "Add VPN client" {
					t.Fatal("unexpected commit metadata")
				}
				return nil, nil
			}
			if name == "but" && args[0] == "pr" {
				if strings.Contains(string(input), "@") || strings.Contains(string(input), "alice") {
					t.Fatal("client identity in PR text")
				}
				if fail {
					return nil, errors.New("forge unavailable")
				}
				return []byte("created https://github.com/sidosera/hm-gitops/pull/123\n"), nil
			}
			t.Fatal("unexpected write operation")
			return nil, nil
		}
		result, err := r.openPR([]string{secretPath}, "Add VPN client", accountPRBody)
		if commits != 1 {
			t.Fatal("did not create exactly one commit")
		}
		if fail {
			if err == nil || !strings.Contains(err.Error(), "preserved") {
				t.Fatal("missing recovery guidance")
			}
		} else if err != nil || result != "https://github.com/sidosera/hm-gitops/pull/123" {
			t.Fatalf("PR failed: %v", err)
		}
	}
}
func TestPorcelainPathsHandleSpacesAndRejectRenames(t *testing.T) {
	paths, err := changedPaths([]byte(" M file with spaces\x00"))
	if err != nil || len(paths) != 1 || paths[0] != "file with spaces" {
		t.Fatal("porcelain path parsing failed")
	}
	if _, err := changedPaths([]byte("R  new\x00old\x00")); err == nil {
		t.Fatal("rename accepted")
	}
}
