# Dynamic Multi-Site DNS Mesh

This repository contains templates and documentation for deploying a resilient, automated, multi-site authoritative DNS architecture using Knot DNS and Kubernetes.

This solution uses **dynamic service discovery**, where each site's IP address is automatically published to a central DNS zone. This removes the need for static IP configurations, making the mesh highly resilient and simplifying operational procedures like adding new sites.

## Table of Contents

- [Architecture Overview](#architecture-overview)
  - [Dynamic Service Discovery](#dynamic-service-discovery)
  - [Site Roles](#site-roles)
- [Prerequisites](#prerequisites)
- [1. One-Time Setup](#1-one-time-setup)
- [2. Deployment Process](#2-deployment-process)
  - [Step 1: Prepare Configuration Files](#step-1-prepare-configuration-files)
  - [Step 2: Deploy Each Site](#step-2-deploy-each-site)
- [3. Operational Procedures](#3-operational-procedures)
  - [Onboarding a New Site (Simplified)](#onboarding-a-new-site-simplified)
  - [Day-to-Day Zone Management](#day-to-day-zone-management)
- [4. Architectural Recommendations](#4-architectural-recommendations)

---

## Architecture Overview

This architecture implements a **multi-primary DNS mesh**. Each site is the primary authority for its own zone and a secondary for all other zones. This provides extremely high read availability.

### Dynamic Service Discovery
This project's key feature is its use of dynamic service discovery to eliminate static IP dependencies.
- **ExternalDNS**: A Kubernetes controller that watches `Service` objects and automatically creates DNS records in a central, resolvable DNS zone.
- **Knot DNS Remotes**: The `remote` blocks in the Knot configurations use stable, fully qualified domain names (FQDNs) instead of IP addresses.
- **Catalog Zones**: Provides a declarative, automated way to manage the list of zones in the mesh.

This combination means that adding a new site only requires updating the catalog; the rest of the mesh discovers the new peer automatically via DNS.

### Site Roles
1.  **Catalog Primary**: The main server responsible for generating the catalog of all zones.
2.  **Catalog Secondary**: A hot standby for the catalog primary.
3.  **Standard**: A consumer of the catalog, primary only for its own local zone.

---

## Prerequisites
- A functional Kubernetes cluster at each site.
- A **resolvable DNS zone** that you can manage (e.g., on AWS Route 53, Google Cloud DNS, or another internal DNS server).
- **ExternalDNS** deployed in each cluster and configured to manage your DNS zone.
- `kubectl` configured with access to each site's cluster.
- A `StorageClass` that supports dynamic provisioning of `PersistentVolumes`.
- `openssl` and `docker` installed locally.

---

## 1. One-Time Setup

**Generate Global Assets:**
Run the `00-one-time-setup.sh` script to generate a shared **TSIG key** and a **TLS certificate**.
```bash
./00-one-time-setup.sh
```
This creates a `02-secrets.yaml` file containing the necessary Kubernetes secrets.

---

## 2. Deployment Process

### Step 1: Prepare Configuration Files

1.  **Configure Service Hostnames (`06-knot-services.yaml`)**:
    For each site, edit the `knot-lb` service and change the `external-dns.alpha.kubernetes.io/hostname` annotation to a unique FQDN within your resolvable zone.
    - **Example for Primary Catalog Site**: `site-primary.dns.internal`
    - **Example for a Standard Site**: `site-standard-1.dns.internal`

2.  **Configure Knot Remotes (`03-knot-config-*.yaml` files)**:
    In all three `03-knot-config-*.yaml` files, edit the `remote` blocks to match the FQDNs you defined in the previous step. The `id` and `address` must be consistent across all files for all sites in the mesh.

3.  **Configure Local Domains (`03-knot-config-*.yaml` files)**:
    In each of the three template files, find the `zone:` section and ensure the `domain` name matches the role (e.g., `catalog-primary.internal.dns` in the primary config).

4.  **Update Catalog Zone (`04-catalog-zone-configmap.yaml`)**:
    Edit the catalog to list the primary domain for every site in your mesh. Ensure the SOA record and PTR records are correct.

### Step 2: Deploy Each Site

For each site in your mesh:

1.  **Apply Namespace and Secrets**:
    ```bash
    kubectl apply -f 01-namespace.yaml
    kubectl apply -f 02-secrets.yaml
    ```

2.  **Choose and Apply the Role-Specific ConfigMap**:
    ```bash
    # For the Catalog Primary site (and ONLY this site), also apply the catalog zone.
    kubectl apply -f 04-catalog-zone-configmap.yaml
    
    # Apply the chosen config for the site's role.
    kubectl apply -f 03-knot-config-cat-primary.yaml # Or -cat-secondary, or -std
    ```

3.  **Deploy Knot Service and StatefulSet**:
    ```bash
    # The service file now contains your site's unique hostname annotation.
    kubectl apply -f 06-knot-services.yaml
    kubectl apply -f 05-knot-statefulset.yaml
    ```
    At this point, ExternalDNS will see the service annotation and create the DNS record.

4.  **Upload Zone File and Initialise**:
    - Upload the site's primary zone file (e.g., `my-site.internal.dns.zone`).
    - Exec into the pod to generate DNSSEC keys, sign the zone, and reload Knot.
      ```bash
      kubectl exec -it knot-0 -n dns-system -- bash
      knotc zone-key-generate my-site.internal.dns ksk+zsk && knotc zone-sign my-site.internal.dns && exit
      kubectl exec -it knot-0 -n dns-system -- knotc reload
      ```

---

## 3. Operational Procedures

### Onboarding a New Site (Simplified)

With the dynamic architecture, adding a new site is dramatically simpler.

1.  **Deploy the New Site**:
    - Follow the "Deploy Each Site" process for the new site.
    - Ensure you have created a unique FQDN for it in its copy of `06-knot-services.yaml` and added its remote entry to your `03-knot-config-*.yaml` templates before you begin.

2.  **Update the Catalog Zone**:
    - Edit **only one file**: `04-catalog-zone-configmap.yaml`.
    - Add the new site's primary zone (e.g., `standard-2.internal.dns`) to the PTR record list.
    - Increment the SOA serial number.

3.  **Apply the Catalog Update**:
    - Apply the updated `ConfigMap` to the **primary catalog site only**.
      ```bash
      # Set context to the primary catalog site's cluster
      kubectl apply -f 04-catalog-zone-configmap.yaml
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload catalog.internal.dns
      ```
The mesh will now automatically discover and begin syncing with the new site.

### Day-to-Day Zone Management
This remains the same. Connect to a zone's primary server via `kubectl exec` and use `knotc` to manage records.

---

## 4. Architectural Recommendations
With dynamic service discovery implemented, consider these next steps for a production-grade environment:

- **Automated Certificate Management**: Use **cert-manager** to automatically issue and renew the TLS certificates used for QUIC, replacing the one-time manual generation.
- **Stateful Backups**: Implement a Kubernetes `CronJob` to regularly back up zone data and DNSSEC keys to off-site object storage.
- **Network Policies**: Use `NetworkPolicy` resources to strictly control which pods can communicate with the Knot DNS servers.
- **Secrets Management**: For maximum security, integrate with a system like **HashiCorp Vault** to manage and rotate the TSIG key dynamically.
- **Configuration Templating**: While much improved, the need to edit three `remote` blocks can be eliminated by using **Helm** or **Kustomize** to generate the configurations from a single list of sites.