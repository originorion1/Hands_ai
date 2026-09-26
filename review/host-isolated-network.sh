#!/usr/bin/env bash
set -euo pipefail
umask 077
mode=${1:-}
namespace=orion-pilot-f7c7955
expected_inode='net:[4026532292]'
host_link=orionp0
pilot_link=orionp1
host_ip=10.254.205.1
pilot_ip=10.254.205.2
address=3.136.183.188
hostname=terrace-beirut.erpmax.me
marker=/etc/orion-pilot/network-provisioned
hosts_dir=/etc/netns/orion-pilot-f7c7955
comment=orion-pilot-f7c7955
require_namespace() {
  test "$(ip netns exec "$namespace" readlink /proc/self/ns/net)" = "$expected_inode"
}
rule_handles() {
  nft -j list chain ip filter DOCKER-USER | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print(" ".join(str(x["rule"]["handle"]) for x in d["nftables"] if "rule" in x and x["rule"].get("comment") == "orion-pilot-f7c7955"))'
}
remove_owned() {
  for handle in $(rule_handles); do nft delete rule ip filter DOCKER-USER handle "$handle"; done
  nft list table ip orion_pilot_nat >/dev/null 2>&1 && nft delete table ip orion_pilot_nat || true
  ip netns exec "$namespace" nft list table inet orion_pilot >/dev/null 2>&1 && ip netns exec "$namespace" nft delete table inet orion_pilot || true
  ip link show "$host_link" >/dev/null 2>&1 && ip link delete "$host_link" || true
  test ! -f "$hosts_dir/hosts" || rm -- "$hosts_dir/hosts"
  test ! -d "$hosts_dir" || rmdir -- "$hosts_dir"
  rm -f -- "$marker"
}
inspect() {
  require_namespace
  stat -c '%F %a %u:%g %n' /etc/orion-pilot /etc/orion-pilot/ca-bundle.pem
  ip -n "$namespace" -br link show
  ip -n "$namespace" route show table main
  ip netns exec "$namespace" nft list table inet orion_pilot
  nft list table ip orion_pilot_nat
  nft list chain ip filter DOCKER-USER | grep -F "$comment" || true
  ip netns exec "$namespace" getent ahosts "$hostname" | awk '{print $1}' | sort -u
  echo 'Inspection only; no customer HTTP or TLS request made.'
}
case "$mode" in
  apply)
    require_namespace
    test ! -e "$marker"
    test ! -e "$hosts_dir"
    ! ip link show "$host_link" >/dev/null 2>&1
    ! ip -n "$namespace" link show "$pilot_link" >/dev/null 2>&1
    test -z "$(ip -n "$namespace" route show table main)"
    test -z "$(ip -n "$namespace" -6 route show table main)"
    nft list chain ip filter DOCKER-USER >/dev/null
    ! nft list table ip orion_pilot_nat >/dev/null 2>&1
    ! ip netns exec "$namespace" nft list table inet orion_pilot >/dev/null 2>&1
    test -z "$(rule_handles)"
    test "$(getent ahosts "$hostname" | awk '{print $1}' | sort -u)" = "$address"
    test "$(cat /proc/sys/net/ipv4/ip_forward)" = 1
    printf '%s\n' APPLYING > "$marker"
    trap 'remove_owned' ERR
    install -d -m 0750 -o root -g orion-pilot "$hosts_dir"
    printf '%s %s\n' "$address" "$hostname" | install -m 0640 -o root -g orion-pilot /dev/stdin "$hosts_dir/hosts"
    ip netns exec "$namespace" nft -f - <<RULES
 table inet orion_pilot {
  chain output { type filter hook output priority -50; policy drop;
   oifname "lo" accept
   ip daddr $address tcp dport 443 accept
  }
  chain input { type filter hook input priority -50; policy drop;
   iifname "lo" accept
   ct state established,related accept
  }
 }
RULES
    nft add table ip orion_pilot_nat
    nft 'add chain ip orion_pilot_nat postrouting { type nat hook postrouting priority 101; policy accept; }'
    nft add rule ip orion_pilot_nat postrouting ip saddr "$pilot_ip" ip daddr "$address" oifname eth0 masquerade
    nft insert rule ip filter DOCKER-USER iifname "$host_link" oifname eth0 ip saddr "$pilot_ip" ip daddr "$address" tcp dport 443 accept comment "$comment"
    nft insert rule ip filter DOCKER-USER iifname eth0 oifname "$host_link" ip saddr "$address" ip daddr "$pilot_ip" ct state established,related accept comment "$comment"
    ip link add "$host_link" type veth peer name "$pilot_link"
    ip link set "$pilot_link" netns "$namespace"
    ip addr add "$host_ip/30" dev "$host_link"
    ip link set "$host_link" up
    ip -n "$namespace" addr add "$pilot_ip/30" dev "$pilot_link"
    ip -n "$namespace" link set "$pilot_link" up
    ip -n "$namespace" route add default via "$host_ip" dev "$pilot_link"
    printf '%s\n' "$expected_inode" > "$marker"
    inspect
    trap - ERR
    ;;
  inspect)
    test -f "$marker"
    inspect
    ;;
  cleanup)
    test -f "$marker"
    test "$(cat "$marker")" = "$expected_inode" || test "$(cat "$marker")" = APPLYING
    require_namespace
    test -z "$(ip netns pids "$namespace")"
    remove_owned
    echo 'Pilot-only veth, NAT, firewall, resolver file and route removed.'
    ;;
  *) echo 'usage: host-isolated-network.sh apply|inspect|cleanup' >&2; exit 2 ;;
esac
