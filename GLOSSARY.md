# Infrastructure services

The infrastructure is described by services and their regional deployments.

## Language

**Gateway service**:
The VPN service that accepts authenticated client traffic and routes it to an exit. It can have deployments in multiple regions with different ingress, routing and egress roles.
_Avoid_: SPB service, UK service, primary service, secondary service

**Region**:
A named deployment location, such as `uk` or `spb`. A region contains service deployments and is independent of their service identity.
_Avoid_: Service (when referring to a location)

**Availability zone**:
A provider-defined failure domain within a region. A zone is recorded separately when known; a city or region alone does not establish an availability zone.
_Avoid_: Region (when referring to an independent failure domain)

**Service deployment**:
A running instance of a service in a region. Regional deployments may perform complementary roles without becoming separate services.

**Host**:
A machine that supplies capacity for service deployments. Its location and cluster role do not define a service.

**Control plane**:
The shared system that manages desired deployment state across regions. A host's control-plane or worker role is separate from its location and from the services it runs.
