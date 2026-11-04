# Infrastructure services

The fleet is described by clusters, placement and services. A location or machine is never a service identity.

## Language

**Cluster**:
The shared deployment and control-plane boundary. `hm-cluster` currently contains two PoPs.
_Avoid_: edge-01 (the former cluster name)

**PoP**:
A point of presence, named `<country>-<city>`, such as `uk-ldn` or `ru-spb`. Country and city are descriptive metadata; a PoP contains one or more zones.

**Zone**:
A named placement zone within a PoP, such as `uk-ldn-1` or `ru-spb-1`. Logical zone names alone do not establish provider availability-zone or failure-domain independence.

**Host**:
A capacity slot within a zone, named `<zone>/<ordinal>`, such as `uk-ldn-1/1`. Its service deployments can change independently of the slot identity.

**Machine**:
The physical or virtual machine occupying a host slot, identified in inventory by its management IP address. A replacement machine can occupy the same host slot with a different IP.

**Service**:
An application capability deployed within the cluster, independent of host and placement. Gateway and VPN Probe are services.
_Avoid_: UK service, SPB service (when referring to placement)

**Gateway service**:
The VPN service that accepts authenticated client traffic and routes it to an exit. Its deployments can have complementary ingress, routing and egress roles.

**Service deployment**:
An instance of a service placed in a zone. Multiple deployments do not create different logical services.

**Control plane**:
The shared system that manages desired deployment state across the cluster. Control-plane and worker roles are separate from placement and service identity.
