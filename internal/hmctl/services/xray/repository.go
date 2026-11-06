package xray

import core "github.com/sidosera/hm-gitops/internal/hmctl"

const secretPath = "conf/xray/credentials-secret.sops.yaml"

// Service-specific configuration methods wrap the shared GitOps repository.
type repository struct{ *core.Repository }
