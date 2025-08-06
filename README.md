# Dynamic & Self-Healing Multi-Site DNS Mesh

This repository contains templates and documentation for deploying a resilient, automated, and **self-healing** multi-site authoritative DNS architecture using Knot DNS and Kubernetes.

This solution achieves true zero-touch peer discovery. A **reconciler** running on each site uses DNS to discover the complete list of peers from a central set of zones. This creates a robust, self-healing system that is ideal for unreliable networks and requires no configuration changes on existing sites when a new site is added.

## Table of Contents

- [Architecture Overview](#architecture-overview)
  - [Core Concepts](#core-concepts)
  - [The Bootstrap Process](#the-bootstrap-process)
- [Prerequisites](#prerequisites)
- [1. One-Time Setup](#1-one-time-setup)
- [2. Deployment Process](#2-deployment-process)
- [3. Operational Procedures](#3-operational-procedures)
  - [Onboarding a New Site (Fully Automated)](#onboarding-a-new-site-fully-automated)
- [4. Architectural Recommendations for Production](#4-architectural-recommendations-for-production)
  - [Security Hardening](#security-hardening)
  - [Resiliency and Operations](#resiliency-and-operations)

---

## Architecture Overview

### Core Concepts

1.  **Central Directory Zone (`dns.internal`)**: The Primary Catalog site hosts a "directory" zone that acts as the single source of truth for peer discovery. It contains `A` records (IPs) and `TXT` records (zone-to-server mappings).

2.  **Catalog Zone (`catalog.internal.dns`)**: The Primary Catalog site hosts a catalog that lists all member zones in the mesh.

3.  **Automated Peer Reconciliation**: A Kubernetes `CronJob` on each site automatically updates its local Knot configuration. It discovers all peers from the central zones and adds any that are missing from its local configuration.

### The Bootstrap Process

A new site only needs a minimal "welcome packet" to join the mesh. The `03-knot-config-*.yaml` templates provide this by defining the `remote` blocks for only the essential **primary and secondary catalog servers**.

When a new site comes online:
1.  It uses this minimal configuration to connect to the primary and download the full catalog and directory zones.
2.  The reconciler `CronJob` then runs, sees the complete list of peers in the downloaded zones, and compares it to its minimal local configuration.
3.  It automatically adds all missing peers to its `knot.conf`, triggers a restart, and becomes a fully integrated member of the mesh.

---

## Prerequisites
- A functional Kubernetes cluster at each site.
- `kubectl` configured with access to each site's cluster.
- A `StorageClass` that supports dynamic provisioning of `PersistentVolumes`.
- `openssl` and `docker` installed locally.

---

## 1. One-Time Setup

Run the `00-one-time-setup.sh` script to generate a shared **TSIG key** and a **TLS certificate**. This creates a `02-secrets.yaml` file.

---

## 2. Deployment Process

1.  **Prepare Initial Configuration**:
    - **Bootstrap Peers (`03-knot-config-*.yaml`)**: Ensure the `remote` blocks contain the FQDNs for your primary and secondary catalog servers.
    - **Directory (`04-dns-internal-zone-configmap.yaml`)**: Add `A` and `TXT` records for the initial sites.
    - **Catalog (`04-catalog-zone-configmap.yaml`)**: Add the zones for the initial sites.

2.  **Deploy Each Site**:
    For each site, apply the manifests.
    ```bash
    # Apply namespace and secrets
    kubectl apply -f 01-namespace.yaml
    kubectl apply -f 02-secrets.yaml

    # On the Primary Site, apply the zone ConfigMaps
    kubectl apply -f 04-dns-internal-zone-configmap.yaml
    kubectl apply -f 04-catalog-zone-configmap.yaml
    
    # Apply the role-specific Knot config
    kubectl apply -f 03-knot-config-cat-primary.yaml # Or -cat-secondary, or -std

    # Deploy Knot and the Reconciler
    kubectl apply -f 05-knot-statefulset.yaml
    kubectl apply -f 06-knot-services.yaml
    kubectl apply -f 07-reconciler-cronjob.yaml
    ```
3.  **Upload Zone File and Initialise**:
    - Upload the site's primary zone file.
    - `exec` into the pod to generate DNSSEC keys, sign the zone, and reload Knot.

---

## 3. Operational Procedures

### Onboarding a New Site (Fully Automated)

1.  **Update Central Configuration on Primary Site**:
    - Edit **`04-dns-internal-zone-configmap.yaml`**: Add the `A` and `TXT` records for the new site. Increment the SOA serial.
    - Edit **`04-catalog-zone-configmap.yaml`**: Add the new site's zone to the catalog. Increment the SOA serial.

2.  **Apply Central Configuration**:
    - Apply the two updated `ConfigMap` files to the **primary catalog site only**.
      ```bash
      kubectl apply -f 04-dns-internal-zone-configmap.yaml
      kubectl apply -f 04-catalog-zone-configmap.yaml
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload dns.internal
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload catalog.internal.dns
      ```
    **That's it.** Existing sites will automatically discover and reconfigure themselves.

3.  **Deploy the New Site**:
    - You can now deploy the new site using the standard `03-knot-config-std.yaml` template. It will bootstrap itself and automatically discover all other peers.
    
---

## 4. Architectural Recommendations for Production

This architecture provides a strong foundation. To prepare it for a production environment, consider the following enhancements.

### Security Hardening

1.  **Automated Certificate Management with a Private CA**:
    - **Why**: The current setup uses a single, shared, manually-created TLS certificate across all sites. If this key is ever compromised, the entire mesh's encryption is broken.
    - **How**: Deploy **cert-manager** to each cluster. Create a private Certificate Authority (CA) and configure each site to automatically request its own unique TLS certificate from this CA. Each site would then be configured to trust the CA, thereby trusting any certificate it has signed.
    - **Benefit**: This provides vastly improved security, as each site has its own private key, and enables fully automated certificate renewals, preventing outages from expired certificates.

2.  **Network Policies**:
    - **Why**: By default, Kubernetes networking is flat, meaning any pod can attempt to communicate with your Knot DNS pods.
    - **How**: Implement Kubernetes `NetworkPolicy` resources to enforce a zero-trust model. Create strict ingress rules that only allow traffic on ports 53 and 853 from the known IP ranges of your other sites, and block all other access.
    - **Benefit**: This dramatically reduces the attack surface of the DNS service.

3.  **Dynamic Secrets Management for TSIG**:
    - **Why**: The shared TSIG key is a static secret that must be manually distributed and rotated.
    - **How**: For a high-security environment, integrate with a secrets management system like **HashiCorp Vault**. A Vault agent can be injected as a sidecar to the Knot pod to fetch the TSIG key at startup and manage its rotation automatically.
    - **Benefit**: This automates the rotation of a critical security credential, adhering to the principle of least privilege and reducing the risk of a compromised key.

4.  **Run as Non-Root**:
    - **Why**: The default container image runs the Knot process as the `root` user.
    - **How**: Use a `securityContext` in the `05-knot-statefulset.yaml` to force the container to run as a non-root user (e.g., `runAsUser: 1000`). This may require adjusting volume permissions.
    - **Benefit**: This is a critical security best practice that significantly limits the blast radius if the container is ever compromised.

### Resiliency and Operations

1.  **Configuration Templating (The Final Step)**:
    - **Why**: The last manual step in the onboarding process is updating the `03-knot-config-*.yaml` templates with the bootstrap peers before deploying a new site.
    - **How**: Adopt **Helm** or **Kustomize**. You would maintain a single list of all site FQDNs in one file (e.g., a `values.yaml`). The tool would then automatically generate the correct `remote` blocks for the bootstrap configuration and could even generate the `dns.internal` zone file.
    - **Benefit**: This creates a true single source of truth for the entire mesh definition, eliminating the final manual edit and preventing configuration drift.

2.  **Intra-Site High Availability**:
    - **Why**: The `StatefulSet` currently runs with `replicas: 1`. If the single Knot pod at a site fails, DNS resolution for that site goes down until the pod is restarted.
    - **How**: Increase the replica count (e.g., `replicas: 2`). This requires a `StorageClass` that supports `ReadWriteMany` (RWX) volumes (like NFS or a cloud provider's file service) so both pods can access the same data. You should also configure pod anti-affinity to ensure the replicas run on different Kubernetes nodes.
    - **Benefit**: This provides high availability within a single site, protecting against node failures.

3.  **Monitoring and Alerting**:
    - **Why**: The current troubleshooting steps are manual. A production system needs proactive monitoring.
    - **How**: Knot DNS can expose a rich set of metrics via its `mod-stats` module. Deploy a monitoring stack like **Prometheus** and **Grafana**. Scrape the Knot metrics to build dashboards that visualize zone transfer rates, query latency, and error counts. Configure **Alertmanager** to send notifications for critical issues like failed zone transfers or a peer being unreachable for an extended period.
    - **Benefit**: This provides crucial visibility into the health of the mesh and enables proactive incident response.

4.  **Stateful Backups**:
    - **Why**: The data in the `PersistentVolume` (zones, keys) is not automatically backed up.
    - **How**: Create a Kubernetes `CronJob` that periodically runs `knotc zone-backup` and pushes the encrypted backup files to a durable, off-site object storage location (like AWS S3 or MinIO).
    - **Benefit**: This protects against data corruption, accidental deletion, and total storage failure, ensuring disaster recovery is possible.