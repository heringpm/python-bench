#!/bin/bash

#### Read in machine file from cli $1

MF=$1

echo "======================CPU====================="
clush --machinefile ${MF} -b cpupower frequency-set -g performance
clush --machinefile ${MF} -b cpupower idle-set -d 1
clush --machinefile ${MF} -b "cpupower frequency-set --governor performance --min 3700000 --max 3700000 && cpupower idle-set -D 1"
echo "======================lustre client====================="

clush --machinefile ${MF} -b << EOT
lctl set_param osc.*OST*.max_pages_per_rpc=16M
lctl set_param osc.*.max_rpcs_in_flight=128
lctl set_param osc.*.max_dirty_mb=512
lctl set_param osc.*.checksums=0
#lctl set_param llite.*.hybrid_io=0
lctl set_param mdc.*.max_rpcs_in_flight=128
lctl set_param mdc.*.max_mod_rpcs_in_flight=127
# Increase RPC timeouts for 768 concurrent operations
# lctl set_param timeout=600
# lctl set_param ldlm_timeout=200
# lctl set_param at_min=10
# lctl set_param at_max=600
# Metadata operation optimizations
lctl set_param llite.*.statahead_max=128
lctl set_param llite.*.statahead_agl=1
# lctl set_param llite.*.dir_stripe_max_hash_size=131072
#lctl set_param ldlm.namespaces.*.lru_size=0
#lctl set_param osc.*.short_io_bytes 65536
lctl set_param ldlm.namespaces.*.lru_max_age=5000
# lctl set_param mdc.*.checksums=1
# For shared directory workload
#lctl set_param llite.*.lazystatfs=1
#lctl set_param llite.*.max_cached_mb=4096
# For compression
# lctl set_param llite.*.enable_compression=1
EOT