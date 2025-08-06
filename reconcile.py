# reconcile.py
import dns.resolver
import dns.zone
import os
import re
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# --- Configuration ---
NAMESPACE = "dns-system"
CONFIGMAP_NAME = "knot-config"
STATEFULSET_NAME = "knot"
CATALOG_ZONE_NAME = "catalog.internal.dns"
KNOT_CONFIG_PATH = "/etc/knot/knot.conf"
# This is the FQDN for the local Knot server inside the pod.
# It resolves via the headless service.
LOCAL_KNOT_SERVER = "knot-0.knot-headless.dns-system.svc.cluster.local"

def get_desired_remotes_from_catalog():
    """
    Performs a zone transfer of the catalog zone to get the desired list of zones.
    Derives the expected remote IDs and FQDNs from the zone names.
    """
    print(f"Attempting AXFR of {CATALOG_ZONE_NAME} from {LOCAL_KNOT_SERVER}...")
    try:
        zone = dns.zone.from_xfr(dns.query.xfr(LOCAL_KNOT_SERVER, CATALOG_ZONE_NAME))
        desired_remotes = {}
        for name, node in zone.nodes.items():
            # Look for PTR records which define member zones
            if str(name) == "version" or str(name) == "@" or not node.rdatasets:
                continue
            for rdataset in node.rdatasets:
                if rdataset.rdtype == dns.rdatatype.PTR:
                    for item in rdataset.items:
                        zone_name = str(item)
                        # Derive remote ID and FQDN from the zone name
                        # e.g., "catalog-primary.internal.dns." -> "catalog-primary-remote", "site-primary.dns.internal"
                        base_name = zone_name.split('.')[0]
                        remote_id = f"{base_name}-remote"
                        fqdn = f"site-{base_name}.dns.internal"
                        desired_remotes[remote_id] = fqdn
        print(f"Found desired remotes in catalog: {list(desired_remotes.keys())}")
        return desired_remotes
    except Exception as e:
        print(f"Error during AXFR of catalog zone: {e}")
        print("This is expected if the local Knot server is not yet ready. Exiting gracefully.")
        exit(0)


def get_current_remotes_from_config():
    """
    Reads the local knot.conf file and parses it to find the currently
    configured remote IDs.
    """
    if not os.path.exists(KNOT_CONFIG_PATH):
        print(f"Error: Knot config file not found at {KNOT_CONFIG_PATH}")
        return None

    with open(KNOT_CONFIG_PATH, 'r') as f:
        content = f.read()

    remotes = set()
    # A simple regex to find 'id:' lines within a 'remote:' block
    remote_section_match = re.search(r'remote:(.*?)(\w+:|$)', content, re.DOTALL)
    if remote_section_match:
        remote_section = remote_section_match.group(1)
        ids = re.findall(r'-    id:  (\S+)', remote_section)
        remotes.update(ids)

    print(f"Found current remotes in config: {list(remotes)}")
    return remotes

def generate_new_config(new_remotes):
    """
    Adds the new remote blocks and updates the ACLs and templates in the
    existing knot.conf content.
    """
    print(f"Generating new config to add remotes: {list(new_remotes.keys())}")
    with open(KNOT_CONFIG_PATH, 'r') as f:
        lines = f.readlines()

    new_config_lines = list(lines)
    
    # 1. Add new remote blocks
    for i, line in enumerate(lines):
        if "remote:" in line:
            for remote_id, fqdn in new_remotes.items():
                new_block = [
                    f"      - id: {remote_id}
",
                    f"        address: {fqdn}@853 # !!! CHANGE THIS to the site's FQDN !!!
",
                    "        key: xfr-key
",
                    "        quic: on
"
                ]
                # Insert after the 'remote:' line
                new_config_lines[i+1:i+1] = new_block
            break

    # Convert back to a single string to perform regex replacements
    new_config_str = "".join(new_config_lines)

    # 2. Add to ACLs and templates
    new_remote_ids_str = ", ".join(new_remotes.keys())
    
    # Regex to find a list and append to it
    def append_to_list(list_name, content):
        pattern = re.compile(f"({list_name}:\s*\[[^\]]*)"")
        return pattern.sub(f"\1, {new_remote_ids_str}", content)

    new_config_str = append_to_list("remote", new_config_str) # For transfer-acl
    new_config_str = append_to_list("master", new_config_str) # For secondary-template

    return new_config_str


def main():
    print("--- Starting Knot Config Reconciler ---")
    try:
        config.load_incluster_config()
        api = client.CoreV1Api()
        apps_api = client.AppsV1Api()
        print("Successfully loaded in-cluster Kubernetes config.")
    except Exception as e:
        print(f"Error loading Kubernetes config: {e}")
        print("This script must be run inside a Kubernetes pod with appropriate RBAC permissions.")
        return

    desired_remotes = get_desired_remotes_from_catalog()
    current_remotes = get_current_remotes_from_config()

    if desired_remotes is None or current_remotes is None:
        print("Could not determine desired or current state. Exiting.")
        return

    missing_remotes_ids = set(desired_remotes.keys()) - current_remotes
    if not missing_remotes_ids:
        print("Configuration is up to date. No changes needed.")
        return

    print(f"Configuration is stale. Missing remotes: {list(missing_remotes_ids)}")
    
    remotes_to_add = {k: v for k, v in desired_remotes.items() if k in missing_remotes_ids}

    # Generate the new knot.conf content
    new_knot_conf = generate_new_config(remotes_to_add)

    # Update the ConfigMap
    try:
        print(f"Fetching ConfigMap '{CONFIGMAP_NAME}'...")
        cm = api.read_namespaced_config_map(name=CONFIGMAP_NAME, namespace=NAMESPACE)
        cm.data["knot.conf"] = new_knot_conf
        
        print(f"Updating ConfigMap '{CONFIGMAP_NAME}'...")
        api.replace_namespaced_config_map(name=CONFIGMAP_NAME, namespace=NAMESPACE, body=cm)
        print("ConfigMap updated successfully.")
    except ApiException as e:
        print(f"Error updating ConfigMap: {e}")
        return

    # Trigger a rolling restart of the StatefulSet
    try:
        print(f"Triggering rolling restart of StatefulSet '{STATEFULSET_NAME}'...")
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "reconciler/restartedAt": dns.resolver.datetime.datetime.utcnow().isoformat()
                        }
                    }
                }
            }
        }
        apps_api.patch_namespaced_stateful_set(name=STATEFULSET_NAME, namespace=NAMESPACE, body=patch)
        print("StatefulSet restart triggered successfully.")
    except ApiException as e:
        print(f"Error restarting StatefulSet: {e}")
        return

    print("--- Reconciler finished successfully ---")

if __name__ == "__main__":
    main()
