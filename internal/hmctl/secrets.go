package hmctl

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"time"

	age "filippo.io/age"
	"github.com/getsops/sops/v3"
	"github.com/getsops/sops/v3/aes"
	sopsage "github.com/getsops/sops/v3/age"
	"github.com/getsops/sops/v3/cmd/sops/common"
	"github.com/getsops/sops/v3/config"
	"github.com/getsops/sops/v3/keyservice"
	sopsyaml "github.com/getsops/sops/v3/stores/yaml"
	"go.yaml.in/yaml/v3"
)

// Only native age identities are accepted. No plugins, shell key commands or remote KMS.
type ageService struct {
	keyservice.UnimplementedKeyServiceServer
	identities sopsage.ParsedIdentities
}

func (s ageService) Decrypt(_ context.Context, req *keyservice.DecryptRequest) (*keyservice.DecryptResponse, error) {
	if req.Key == nil || req.Key.GetAgeKey() == nil {
		return nil, errors.New("only native age recipients are supported")
	}
	key, err := sopsage.MasterKeyFromRecipient(req.Key.GetAgeKey().Recipient)
	if err != nil {
		return nil, errors.New("invalid age recipient")
	}
	key.EncryptedKey = string(req.Ciphertext)
	s.identities.ApplyToMasterKey(key)
	plain, err := key.Decrypt()
	if err != nil {
		return nil, errors.New("age identity cannot decrypt this Secret")
	}
	return &keyservice.DecryptResponse{Plaintext: plain}, nil
}

type secretDocument struct {
	ctx      context.Context
	tree     sops.Tree
	store    *sopsyaml.Store
	key      []byte
	document yaml.Node
	data     *yaml.Node
	configs  map[string]object
	original map[string][]byte
}

func mapping(node *yaml.Node, name string) *yaml.Node {
	if node.Kind == yaml.DocumentNode && len(node.Content) > 0 {
		node = node.Content[0]
	}
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == name {
			return node.Content[i+1]
		}
	}
	return nil
}
func (r *repository) loadSecret() (*secretDocument, error) {
	path := r.keyFile
	if path == "" {
		path = os.Getenv("SOPS_AGE_KEY_FILE")
	}
	if path == "" {
		return nil, errors.New("set SOPS_AGE_KEY_FILE to the existing private age identity")
	}
	info, statErr := os.Stat(path)
	if statErr != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || !ownedByUser(info) {
		return nil, errors.New("age identity must be an owned regular file with permissions 0600")
	}
	identity, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("cannot read age identity file")
	}
	defer clear(identity)
	parsed, err := age.ParseIdentities(bytes.NewReader(identity))
	if err != nil || len(parsed) == 0 {
		return nil, errors.New("invalid native age identity file")
	}
	for _, id := range parsed {
		if _, ok := id.(*age.X25519Identity); !ok {
			return nil, errors.New("only native X25519 age identities are supported")
		}
	}
	raw, err := r.root.ReadFile(secretPath)
	if err != nil {
		return nil, errors.New("cannot read encrypted Secret")
	}
	store := sopsyaml.NewStore(&config.YAMLStoreConfig{Indent: 4})
	tree, err := store.LoadEncryptedFile(raw)
	if err != nil {
		return nil, errors.New("invalid SOPS Secret")
	}
	if tree.Metadata.EncryptedRegex != "^(data|stringData)$" {
		return nil, errors.New("unexpected SOPS encryption policy; refusing to weaken Secret encryption")
	}
	for _, group := range tree.Metadata.KeyGroups {
		for _, master := range group {
			key, ok := master.(*sopsage.MasterKey)
			if !ok {
				return nil, errors.New("Secret must use native age recipients")
			}
			if _, err = age.ParseX25519Recipient(key.Recipient); err != nil {
				return nil, errors.New("unsupported age recipient")
			}
		}
	}
	service := keyservice.NewCustomLocalClient(ageService{identities: sopsage.ParsedIdentities(parsed)})
	key, err := common.DecryptTree(common.DecryptTreeOpts{Tree: &tree, Cipher: aes.NewCipher(), KeyServices: []keyservice.KeyServiceClient{service}})
	if err != nil {
		return nil, errors.New("Secret decryption/MAC verification failed; check the age identity")
	}
	plain, err := store.EmitPlainFile(tree.Branches)
	if err != nil {
		clear(key)
		return nil, errors.New("cannot decode Secret")
	}
	defer clear(plain)
	var document yaml.Node
	if yaml.Unmarshal(plain, &document) != nil {
		clear(key)
		return nil, errors.New("invalid decrypted Secret")
	}
	data := mapping(&document, "data")
	if data == nil || data.Kind != yaml.MappingNode {
		clear(key)
		return nil, errors.New("Secret has no data mapping")
	}
	d := &secretDocument{ctx: r.ctx, tree: tree, store: store, key: key, document: document, data: data, configs: map[string]object{}, original: map[string][]byte{}}
	for i := 0; i+1 < len(data.Content); i += 2 {
		name := data.Content[i].Value
		value, err := base64.StdEncoding.DecodeString(data.Content[i+1].Value)
		if err != nil {
			d.close()
			return nil, errors.New("invalid Secret data encoding")
		}
		d.original[name] = value
		if zone, ok := gatewayZone(name); ok {
			c, err := decodeJSON(value)
			if err != nil {
				d.close()
				return nil, err
			}
			d.configs[zone] = c
		}
	}
	return d, nil
}
func gatewayZone(name string) (string, bool) {
	const suffix = ".gateway.json"
	if len(name) <= len(suffix) || name[len(name)-len(suffix):] != suffix {
		return "", false
	}
	return name[:len(name)-len(suffix)], true
}
func (d *secretDocument) close() {
	clear(d.key)
	for _, v := range d.original {
		clear(v)
	}
}
func (d *secretDocument) setJSON(name string, c object) error {
	encoded, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return errors.New("cannot encode Xray config")
	}
	node := mapping(d.data, name)
	if node == nil {
		d.data.Content = append(d.data.Content, &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: name}, &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str"})
		node = d.data.Content[len(d.data.Content)-1]
	}
	node.Value = base64.StdEncoding.EncodeToString(encoded)
	return nil
}
func (d *secretDocument) encode() ([]byte, error) {
	for zone, c := range d.configs {
		name := zone + ".gateway.json"
		original, err := decodeJSON(d.original[name])
		if err != nil {
			return nil, err
		}
		oldJSON, _ := json.Marshal(original)
		newJSON, _ := json.Marshal(c)
		if !bytes.Equal(oldJSON, newJSON) {
			if err = d.setJSON(name, c); err != nil {
				return nil, err
			}
		}
	}
	plain, err := yaml.Marshal(&d.document)
	if err != nil {
		return nil, errors.New("cannot encode Secret document")
	}
	defer clear(plain)
	branches, err := d.store.LoadPlainFile(plain)
	if err != nil {
		return nil, errors.New("cannot prepare encrypted Secret")
	}
	d.tree.Branches = branches
	// Workload revisions use SOPS lastmodified, which is serialized to whole seconds.
	// Ensure distinct mutations cannot accidentally reuse the same rollout revision.
	wait := time.Until(d.tree.Metadata.LastModified.Truncate(time.Second).Add(time.Second))
	if wait > 2*time.Second {
		return nil, errors.New("SOPS timestamp is in the future; correct the system clock before updating")
	}
	if wait > 0 {
		timer := time.NewTimer(wait)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-d.ctx.Done():
			return nil, errors.New("Secret update cancelled")
		}
	}
	if err = common.EncryptTree(common.EncryptTreeOpts{Tree: &d.tree, Cipher: aes.NewCipher(), DataKey: d.key}); err != nil {
		return nil, errors.New("cannot encrypt Secret")
	}
	encrypted, err := d.store.EmitEncryptedFile(d.tree)
	if err != nil {
		return nil, errors.New("cannot serialize encrypted Secret")
	}
	return encrypted, nil
}
