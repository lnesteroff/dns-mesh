# Multi-Site DNS Mesh

This repository contains templates and documentation for deploying a resilient, automated, multi-site authoritative DNS architecture using Knot DNS on Kubernetes.

The solution is designed for challenging network environments (low-bandwidth, high-latency, air-gapped) and prioritises resilience, automation, and security. By using a role-based approach (`catalog-primary`, `catalog-secondary`, `standard`), the provided files act as generic templates that can be adapted for any number of sites.

## Table of Contents

- [Architecture Overview](#architecture-overview)
  - [Site Roles](#site-roles)
- [Prerequisites](#prerequisites)
- [1. One-Time Setup](#1-one-time-setup)
- [2. Deployment Process](#2-deployment-process)
  - [Step 1: Prepare Configuration Files](#step-1-prepare-configuration-files)
  - [Step 2: Deploy Each Site](#step-2-deploy-each-site)
- [3. Operational Procedures](#3-operational-procedures)
  - [Onboarding a New Site](#onboarding-a-new-site)
  - [Day-to-Day Zone Management](#day-to-day-zone-management)
- [4. DNSSEC Forwarding with Knot Resolver](#4-dnssec-forwarding-with-knot-resolver)
- [5. Monitoring & Troubleshooting](#5-monitoring--troubleshooting)
- [6. Future Improvements](#6-future-improvements)

---

## Architecture Overview

This architecture implements a **multi-primary DNS mesh**. Each site is the primary authority for its own zone and a secondary for all other zones. This provides extremely high read availability.

**Key Technologies:**
- **Knot DNS**: A high-performance authoritative DNS server.
- **Zone Transfers over QUIC (XoQ)**: Ensures reliable and encrypted zone synchronisation.
- **Catalog Zones**: Provides a declarative, automated way to manage the fleet of zones.
- **DNSSEC**: Secures zones against spoofing and manipulation.
- **Kubernetes StatefulSets**: Manages the Knot DNS pods with stable network identity and storage.

### Site Roles
This project uses three distinct roles for DNS sites:
1.  **Catalog Primary**: The main server responsible for generating the catalog of all zones in the mesh. There should be only **one** of these.
2.  **Catalog Secondary**: A hot standby for the catalog primary. It maintains a copy of the catalog and can be promoted if the primary fails. There should be at least **one** of these.
3.  **Standard**: A consumer of the catalog. These sites are primary only for their own local zone and secondary for all others.

---

## Prerequisites
- A functional Kubernetes cluster at each site.
- `kubectl` configured with access to each site's cluster.
- A `StorageClass` that supports dynamic provisioning of `PersistentVolumes`.
- The following utilities installed locally:
  - `openssl`
  - `docker` (to run `keymgr` from the official Knot image)

---

## 1. One-Time Setup

**Generate Global Assets:**
The `00-one-time-setup.sh` script automates the creation of assets that must be shared across all sites: a **TSIG key** for authentication and a **TLS certificate** for encryption.
```bash
# The script requires Docker to be running
./00-one-time-setup.sh
```
This creates a `02-secrets.yaml` file containing the necessary Kubernetes secrets.

---

## 2. Deployment Process

### Step 1: Prepare Configuration Files

Before deploying, you must customize the configuration files for your environment.

1.  **Update Remote IPs**:
    In all three `03-knot-config-*.yaml` files, find the `remote:` section and replace the placeholder IP addresses (`10.x.0.10`) with the actual external IP addresses of your sites. Ensure the `id` for each remote is unique and descriptive.

2.  **Update Primary Domains**:
    In each `03-knot-config-*.yaml` file, find the `zone:` section and change the example domain (`catalog-primary.internal.dns`, etc.) to the real domain name for that site role.

3.  **Update Catalog Zone**:
    In `04-catalog-zone-configmap.yaml`, update the list of member zones. You must replace the example PTR records with entries for every zone in your mesh. Remember to update the SOA record and generate the correct hashes for your zone names.

### Step 2: Deploy Each Site

For each site in your mesh, follow these instructions:

1.  **Choose the Right ConfigMap**:
    Select **one** of the `03-knot-config-*.yaml` files that matches the site's role (e.g., `03-knot-config-cat-primary.yaml` for your main catalog server).

2.  **Apply Namespace and Secrets**:
    ```bash
    # Set your kubectl context to the target site's cluster
    kubectl apply -f 01-namespace.yaml
    kubectl apply -f 02-secrets.yaml
    ```

3.  **Apply the Role-Specific ConfigMap**:
    ```bash
    # For the Catalog Primary site (and ONLY this site), also apply the catalog zone.
    kubectl apply -f 04-catalog-zone-configmap.yaml
    
    # Apply the chosen config for the site's role.
    kubectl apply -f 03-knot-config-cat-primary.yaml # Or -cat-secondary, or -std
    ```

4.  **Deploy Knot**:
    ```bash
    # These files are the same for all sites.
    kubectl apply -f 05-knot-statefulset.yaml
    kubectl apply -f 06-knot-services.yaml
    ```

5.  **Upload Zone File and Initialise**:
    - Upload the site's primary zone file to the new pod (e.g., `my-site.internal.dns.zone`).
      ```bash
      # Replace with your actual zone file and pod name
      kubectl cp my-site.internal.dns.zone knot-0:/var/lib/knot/zones/ -n dns-system
      ```
    - Exec into the pod to generate DNSSEC keys, sign the zone, and reload Knot.
      ```bash
      kubectl exec -it knot-0 -n dns-system -- bash
      
      # Inside the pod (replace with your actual domain)
      knotc zone-key-generate my-site.internal.dns ksk+zsk
      knotc zone-sign my-site.internal.dns
      exit

      # Reload knot to apply changes
      kubectl exec -it knot-0 -n dns-system -- knotc reload
      ```

Repeat this process for every site, ensuring you use the correct configuration file for each role.

---

## 3. Operational Procedures

**Onboarding a New Site**
1. **Update All Configurations**: Add a new `remote:` block for the new site to all three `03-knot-config-*.yaml` files and add the new remote to the `acl` and `template` lists.
2. **Update Catalog Zone**: Edit `04-catalog-zone-configmap.yaml` to add the new site's zone to the catalog. Increment the SOA serial.
3. **Apply Changes**: Roll out the updated `ConfigMap` files to all existing sites and restart the `knot` StatefulSet.
4. **Deploy New Site**: Follow the process in "Deploy Each Site" to launch the new standard site.

**Day-to-Day Zone Management**
To edit a record, connect to the primary server for that zone and use a `knotc` transaction.
```bash
# Exec into the pod on the zone's primary site
kubectl exec -it knot-0 -n dns-system -- bash

# Use a transaction to safely edit the zone
knotc zone-begin <zone.name>
knotc zone-set <zone.name> <record> <ttl> <type> <value>
knotc zone-commit <zone.name>
exit
```

---

## 4. DNSSEC Forwarding with Knot Resolver
The `07-knot-resolver.yaml` manifest deploys an optional DNSSEC-validating recursive resolver. This is useful for securely forwarding queries to another organisation's DNS.

---

## 5. Monitoring & Troubleshooting
- **Check Pod Logs**: `kubectl logs -f statefulset/knot -n dns-system`
- **Check Zone Status**: `knotc zone-status <zone-name>` (inside the pod)
- **Check Remotes**: `knotc -c /etc/knot/knot.conf remote-check`
- **Manual Transfer Test**: Use `kdig` to test XoQ directly.
  ```bash
  # The TSIG secret can be found in the auto-generated '02-secrets.yaml'
  # or by decoding the Kubernetes secret:
  # kubectl get secret knot-tsig-secret -n dns-system -o jsonpath='{.data.tsig-secret}' | base64 -d
  kdig +quic @<REMOTE_IP> -p 853 \
       -y <key-name>:"<YOUR_TSIG_SECRET_HERE>" \
       <zone.name> AXFR
  ```

---

## 6. Future Improvements
**Configuration Management with Helm/Kustomize**: Adopt a tool like Kustomize or Helm to template the configurations. This would centralize the list of remotes and make adding/removing sites much simpler.

**Automation of Operational Tasks**: Develop automation scripts (e.g., Bash, Python) to streamline tasks like site onboarding.

**Advanced Monitoring and Alerting**: Integrate with a monitoring stack like Prometheus and Grafana. Knot DNS can expose a rich set of metrics that can be used to build dashboards and configure alerts.

**GitOps for Zone Management**: Store zone files in a dedicated Git repository. A CI/CD pipeline could automatically validate and apply changes to the appropriate primary server, providing a full audit trail.