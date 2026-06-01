#!/usr/bin/env bash
set -euo pipefail

NS_NAME="${NETDOC_QUALITY_NS:-netdoc_quality}"
HOST_IF="${NETDOC_QUALITY_HOST_IF:-ndq-host}"
NS_IF="${NETDOC_QUALITY_NS_IF:-ndq-ns}"
HOST_ADDR="${NETDOC_QUALITY_HOST_ADDR:-10.200.0.1}"
NS_ADDR="${NETDOC_QUALITY_NS_ADDR:-10.200.0.2}"
UNREACHABLE_ADDR="${NETDOC_QUALITY_UNREACHABLE_ADDR:-10.200.0.254}"
CIDR="${NETDOC_QUALITY_CIDR:-24}"
DELAY_MS="${NETDOC_QUALITY_DELAY_MS:-220}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_BIN="${NETDOC_CONDA_BIN:-/home/achewwa/miniconda3/bin/conda}"
CONDA_ENV="${NETDOC_CONDA_ENV:-net_doc}"

usage() {
  cat <<EOF
Usage: sudo $0 <setup|run|cleanup|status>

Creates an isolated Linux network namespace for NetDoc network_quality validation.

Targets inside the namespace:
  ${HOST_ADDR}          reachable veth peer, delayed by tc netem when tc is available
  ${UNREACHABLE_ADDR}   same-subnet address with no peer, expected to show packet loss

Environment overrides:
  NETDOC_QUALITY_DELAY_MS=220
  NETDOC_CONDA_BIN=/home/achewwa/miniconda3/bin/conda
  NETDOC_CONDA_ENV=net_doc
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This command needs root. Re-run with sudo." >&2
    exit 1
  fi
}

cleanup() {
  ip netns del "${NS_NAME}" 2>/dev/null || true
  ip link del "${HOST_IF}" 2>/dev/null || true
}

setup() {
  require_root
  cleanup

  ip netns add "${NS_NAME}"
  ip link add "${HOST_IF}" type veth peer name "${NS_IF}"
  ip link set "${NS_IF}" netns "${NS_NAME}"

  ip addr add "${HOST_ADDR}/${CIDR}" dev "${HOST_IF}"
  ip link set "${HOST_IF}" up

  ip netns exec "${NS_NAME}" ip addr add "${NS_ADDR}/${CIDR}" dev "${NS_IF}"
  ip netns exec "${NS_NAME}" ip link set lo up
  ip netns exec "${NS_NAME}" ip link set "${NS_IF}" up

  if command -v tc >/dev/null 2>&1; then
    tc qdisc replace dev "${HOST_IF}" root netem delay "${DELAY_MS}ms"
  else
    echo "tc not found; reachable target will not be artificially delayed." >&2
  fi

  status
}

status() {
  require_root
  echo "namespace: ${NS_NAME}"
  ip netns list | grep -F "${NS_NAME}" || true
  echo
  echo "host interface:"
  ip addr show dev "${HOST_IF}" 2>/dev/null || true
  echo
  echo "namespace interface:"
  ip netns exec "${NS_NAME}" ip addr show dev "${NS_IF}" 2>/dev/null || true
  echo
  echo "qdisc:"
  tc qdisc show dev "${HOST_IF}" 2>/dev/null || true
}

run_quality() {
  require_root
  if ! ip netns list | grep -q "^${NS_NAME}"; then
    echo "Namespace ${NS_NAME} does not exist. Run setup first." >&2
    exit 1
  fi

  if [[ -x "${CONDA_BIN}" ]]; then
    ip netns exec "${NS_NAME}" env PYTHONPATH="${PROJECT_ROOT}/src" \
      "${CONDA_BIN}" run -n "${CONDA_ENV}" python \
      "${PROJECT_ROOT}/scripts/run_network_quality.py" \
      "${HOST_ADDR}" "${UNREACHABLE_ADDR}" --count 4 --timeout 1
  else
    ip netns exec "${NS_NAME}" env PYTHONPATH="${PROJECT_ROOT}/src" \
      python "${PROJECT_ROOT}/scripts/run_network_quality.py" \
      "${HOST_ADDR}" "${UNREACHABLE_ADDR}" --count 4 --timeout 1
  fi
}

main() {
  case "${1:-}" in
    setup)
      setup
      ;;
    run)
      run_quality
      ;;
    cleanup)
      require_root
      cleanup
      ;;
    status)
      status
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
