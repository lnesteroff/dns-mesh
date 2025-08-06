# Dynamic & Self-Healing Multi-Site DNS Mesh

This repository contains templates and documentation for deploying a resilient, automated, and **self-healing** multi-site authoritative DNS architecture using Knot DNS and Kubernetes.

This solution uses a **centralized address book** (`dns.internal` zone) and **catalog zones** for dynamic zone provisioning. A **reconciler CronJob** runs on each site, automatically detecting new peers from the catalog and updating its own configuration. This creates a robust, self-healing system that solves the "stale peer" problem for disconnected sites and makes onboarding a new site a zero-touch operation for existing peers.

## Table of Contents

- [Architecture Overview](#architecture-overview)
  - [Core Concepts](#core-concepts)
  - [Site Roles](#site-roles)
- [Prerequisites](#prerequisites)
- [1. One-Time Setup](#1-one-time-setup)
- [2. Deployment Process](#2-deployment-process)
- [3. Operational Procedures](#3-operational-procedures)
  - [Onboarding a New Site (Fully Automated)](#onboarding-a-new-site-fully-automated)
  - [Day-to-Day Zone Management](#day-to-day-zone-management)
- [4. Architectural Recommendations for Production](#4-architectural-recommendations-for-production)

---

## Architecture Overview

### Core Concepts

1.  **Centralized Address Book (`dns.internal` zone)**: The Primary Catalog site hosts the `dns.internal` zone, which contains `A` records for every site in the mesh. All other sites are secondaries for this zone, giving them a dynamic, centrally managed "address book."

2.  **Dynamic Zone Provisioning (`catalog.internal.dns` zone)**: The Primary Catalog site hosts a catalog that lists all member zones. Other sites consume this catalog to automatically provision themselves as secondaries.

3.  **Automated Peer Reconciliation (CronJob Operator)**: A Kubernetes `CronJob` runs periodically on every site. It compares the list of zones in the catalog with the list of peers in its local `knot.conf`. If a peer is missing (e.g., a new site was added while this one was offline), the reconciler automatically updates its own `ConfigMap` and triggers a restart of its Knot pod to load the new configuration.

This three-part system creates a fully automated mesh where a site that has been offline can reconnect, download the latest address book and catalog, and automatically reconfigure itself to connect to any new peers.

### Site Roles
- **Catalog Primary**: The main server, primary for both the catalog and the `dns.internal` address book zone.
- **Catalog Secondary**: A hot standby, secondary for both zones.
- **Standard**: A consumer, secondary for both zones.

---

## Prerequisites
- A functional Kubernetes cluster at each site.
- `kubectl` configured with access to each site's cluster.
- A `StorageClass` that supports dynamic provisioning of `PersistentVolumes`.
- `openssl` and `docker` installed locally.
- A container registry to host the reconciler image (optional, but recommended for production).

---

## 1. One-Time Setup

Run the `00-one-time-setup.sh` script to generate a shared **TSIG key** and a **TLS certificate**. This creates a `02-secrets.yaml` file.

---

## 2. Deployment Process

The process is the same for every site, including the reconciler.

1.  **Prepare Configuration Files**:
    - **Configure Knot Remotes (`03-knot-config-*.yaml`)**: Ensure the `remote` blocks contain a complete list of the initial sites.
    - **Create Address Book (`04-dns-internal-zone-configmap.yaml`)**: Add `A` records for the initial sites.
    - **Update Catalog (`04-catalog-zone-configmap.yaml`)**: Add the zones for the initial sites.

2.  **Apply Namespace and Secrets**:
    ```bash
    kubectl apply -f 01-namespace.yaml
    kubectl apply -f 02-secrets.yaml
    ```

3.  **Apply Role-Specific ConfigMap(s)**:
    ```bash
    # For the Primary Catalog site, apply both zone ConfigMaps.
    kubectl apply -f 04-dns-internal-zone-configmap.yaml
    kubectl apply -f 04-catalog-zone-configmap.yaml
    
    # Apply the chosen config for the site's role.
    kubectl apply -f 03-knot-config-cat-primary.yaml # Or -cat-secondary, or -std
    ```

4.  **Deploy Knot & Reconciler**:
    ```bash
    kubectl apply -f 05-knot-statefulset.yaml
    kubectl apply -f 06-knot-services.yaml
    # The reconciler is deployed to all sites.
    kubectl apply -f 08-reconciler-cronjob.yaml
    ```

5.  **Upload Zone File and Initialise**:
    - Upload the site's primary zone file.
    - `exec` into the pod to generate DNSSEC keys, sign the zone, and reload Knot.

---

## 3. Operational Procedures

### Onboarding a New Site (Fully Automated)

Onboarding a new site no longer requires manually updating every existing site.

1.  **Update Central Configuration on Primary Site**:
    - Edit **`04-dns-internal-zone-configmap.yaml`**: Add the `A` record for the new site. Increment the SOA serial.
    - Edit **`04-catalog-zone-configmap.yaml`**: Add the new site's zone to the catalog. Increment the SOA serial.

2.  **Apply Central Configuration**:
    - Apply the two updated `ConfigMap` files to the **primary catalog site only**.
      ```bash
      # Set context to the primary catalog site's cluster
      kubectl apply -f 04-dns-internal-zone-configmap.yaml
      kubectl apply -f 04-catalog-zone-configmap.yaml
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload dns.internal
      kubectl exec -it knot-0 -n dns-system -- knotc zone-reload catalog.internal.dns
      ```
    **That's it.** Over the next 5 minutes, the reconciler `CronJob` on every existing site will detect the change, automatically add the new peer to its own configuration, and restart itself.

3.  **Deploy the New Site**:
    - You can now deploy the new site.
    - **Important**: The `03-knot-config-*.yaml` templates still need to be updated with the full list of remotes before you deploy a *new* site, so that the new site is aware of all its peers at launch.

### Day-to-Day Zone Management
This remains the same. Connect to a zone's primary server via `kubectl exec` and use `knotc` to manage records.

---

## 4. Architectural Recommendations for Production
- **Configuration Templating**: The final step to full automation is to use **Helm** or **Kustomize**. This would allow you to define the list of sites in a single file and automatically generate all `remote` blocks and the `dns.internal` zone file, eliminating the last manual step in the onboarding process.
- **Automated Certificate Management**: Use **cert-manager** with a private CA to provide unique, auto-renewing TLS certificates for each site.
- **Stateful Backups**: Implement a Kubernetes `CronJob` to regularly back up zone data to off-site object storage.
- **Network Policies**: Use `NetworkPolicy` resources to strictly control traffic between pods.