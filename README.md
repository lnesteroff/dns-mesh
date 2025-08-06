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
  - [Managing DNS Records](#managing-dns-records)
- [4. Architectural Recommendations for Production](#4-architectural-recommendations-for-production)
  - [The Ultimate Goal: A DNS Operator for Zone Management](#the-ultimate-goal-a-dns-operator-for-zone-management)
  - [Security Hardening](#security-hardening)
  - [Resiliency and Operations](#resiliency-and-operations)

---

## Architecture Overview

### Core Concepts

1.  **Central Directory Zone (`dns.internal`)**: The Primary Catalog site hosts a "directory" zone that acts as the single source of truth for peer discovery. It contains `A` records (IPs) and `TXT` records (zone-to-server mappings).

2.  **Catalog Zone (`catalog.internal.dns`)**: The Primary Catalog site hosts a catalog that lists all member zones in the mesh.

3.  **Automated Peer Reconciliation**: A Kubernetes `CronJob` on each site automatically updates its local Knot configuration. It discovers all peers from the central zones and adds any that are missing from its local configuration.

### The Bootstrap Process

A new site only needs a minimal "welcome packet" to join the mesh. The `03-knot-config-*.yaml` templates provide this by defining the `remote` blocks for only the essential **primary and secondary catalog servers**. When a new site comes online, it connects to the primary, downloads the central zones, and the reconciler automatically discovers and configures all other peers.

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
    # Apply namespace, secrets, and role-specific Knot config
    kubectl apply -f 01-namespace.yaml
    kubectl apply -f 02-secrets.yaml
    kubectl apply -f 03-knot-config-cat-primary.yaml # Or -cat-secondary, or -std

    # On the Primary Site, apply the zone ConfigMaps
    kubectl apply -f 04-dns-internal-zone-configmap.yaml
    kubectl apply -f 04-catalog-zone-configmap.yaml
    
    # Deploy Knot and the Reconciler
    kubectl apply -f 05-knot-statefulset.yaml
    kubectl apply -f 06-knot-services.yaml
    kubectl apply -f 07-reconciler-cronjob.yaml
    ```
3.  **Upload Zone File and Initialise**:
    - Upload the site's primary zone file and use `kubectl exec` to initialize DNSSEC.

---

## 3. Operational Procedures

### Onboarding a New Site (Fully Automated)

1.  **Update Central Configuration on Primary Site**:
    - Edit **`04-dns-internal-zone-configmap.yaml`**: Add the `A` and `TXT` records for the new site. Increment the SOA serial.
    - Edit **`04-catalog-zone-configmap.yaml`**: Add the new site's zone to the catalog. Increment the SOA serial.

2.  **Apply Central Configuration**:
    - Apply the two updated `ConfigMap` files to the **primary catalog site only** and reload the zones.
      ```bash
      kubectl apply -f 04-dns-internal-zone-configmap.yaml
      kubectl apply -f 04-catalog-zone-configmap.yaml
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload dns.internal catalog.internal.dns
      ```
    Existing sites will automatically discover and reconfigure themselves.

3.  **Deploy the New Site**:
    - Deploy the new site using the standard template. It will bootstrap and discover all peers automatically.

### Managing DNS Records

There are two workflows for managing DNS records:

**1. Central Zones (`dns.internal`, `catalog.internal.dns`)**
The source of truth is the YAML `ConfigMap` files. To add a new site, you edit the YAML and apply it to the primary server.

**2. Standard Site Zones (e.g., `standard-1.internal.dns`)**
The source of truth is the live Knot daemon. The recommended method is to use `kubectl exec` to run `knotc` commands. While this works, it is an imperative, manual process. For a true GitOps workflow, a DNS Operator is the recommended solution.

---

## 4. Architectural Recommendations for Production

This architecture provides a strong foundation. To prepare it for a production environment, consider the following enhancements.

### The Ultimate Goal: A DNS Operator for Zone Management

The final step to creating a truly cloud-native and declarative system is to replace the manual `knotc` commands with a Kubernetes Operator.

- **Concept**: An operator is a custom controller that extends the Kubernetes API. You would create a **Custom Resource Definition (CRD)** called `DnsRecord`. Instead of using `kubectl exec`, you would manage records by writing simple YAML files and applying them, enabling a fully declarative, GitOps-friendly workflow.
- **Example `DnsRecord` YAML**:
  ```yaml
  apiVersion: "dns.mesh.io/v1alpha1"
  kind: "DnsRecord"
  metadata:
    name: "new-server-in-standard-1"
  spec:
    zone: "standard-1.internal.dns"
    name: "new-server"
    ttl: 3600
    type: "A"
    value: "10.10.1.5"
  ```
- **How it Works**: The operator would watch for these `DnsRecord` objects and automatically execute the necessary `knotc` commands on the correct primary pod to keep the live state in sync with your declared records.

### Security Hardening

1.  **Automated Certificate Management with a Private CA**:
    - **Why**: The current setup uses a single, shared, manually-created TLS certificate. A compromise of this key would break the entire mesh's encryption.
    - **How**: Deploy **cert-manager** to each cluster. Create a private Certificate Authority (CA) and configure each site to automatically request its own unique TLS certificate from this CA.
    - **Benefit**: This provides vastly improved security, as each site has its own private key, and enables fully automated certificate renewals.

2.  **Network Policies**:
    - **Why**: By default, any pod can attempt to communicate with your Knot DNS pods.
    - **How**: Implement Kubernetes `NetworkPolicy` resources to enforce a zero-trust model, allowing traffic only on required ports from known mesh sites.
    - **Benefit**: This dramatically reduces the attack surface of the DNS service.

3.  **Dynamic Secrets Management for TSIG**:
    - **Why**: The shared TSIG key is a static secret that must be manually distributed and rotated.
    - **How**: For a high-security environment, integrate with a secrets management system like **HashiCorp Vault** to manage and rotate the TSIG key automatically.
    - **Benefit**: This automates the rotation of a critical security credential.

4.  **Run as Non-Root**:
    - **Why**: The default container image runs the Knot process as the `root` user.
    - **How**: Use a `securityContext` in the `05-knot-statefulset.yaml` to force the container to run as a non-root user.
    - **Benefit**: This is a critical security best practice that significantly limits the blast radius if the container is compromised.

### Resiliency and Operations

1.  **Configuration Templating**:
    - **Why**: The last manual step in the onboarding process is updating the `03-knot-config-*.yaml` templates with the bootstrap peers before deploying a new site.
    - **How**: Adopt **Helm** or **Kustomize**. You would maintain a single list of all site FQDNs in one file, and the tool would automatically generate the correct bootstrap configurations and the `dns.internal` zone file.
    - **Benefit**: This creates a true single source of truth for the entire mesh definition.

2.  **Intra-Site High Availability**:
    - **Why**: The `StatefulSet` currently runs with `replicas: 1`. If the single Knot pod at a site fails, DNS for that site goes down.
    - **How**: Increase the replica count (e.g., `replicas: 2`). This requires a `StorageClass` that supports `ReadWriteMany` (RWX) volumes and pod anti-affinity.
    - **Benefit**: This provides high availability within a single site, protecting against node failures.

3.  **Monitoring and Alerting**:
    - **Why**: A production system needs proactive monitoring.
    - **How**: Deploy a monitoring stack like **Prometheus** and **Grafana**. Use Knot's `mod-stats` module to expose metrics and build dashboards and alerts for critical issues like failed zone transfers.
    - **Benefit**: This provides crucial visibility into the health of the mesh.

4.  **Stateful Backups**:
    - **Why**: The data in the `PersistentVolume` is not automatically backed up.
    - **How**: Create a Kubernetes `CronJob` that periodically runs `knotc zone-backup` and pushes the encrypted backup files to a durable, off-site object storage location.
    - **Benefit**: This protects against data loss and ensures disaster recovery is possible.