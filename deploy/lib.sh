# Shared helpers for the deploy scripts (sourced, not run).

# Run a command until it succeeds. IAM is eventually consistent: a service
# account created a moment ago can be reported as "does not exist" when it's
# added to a policy, for up to a minute or two.
retry() {
  local attempt=1 max=12
  until "$@"; do
    if [ "$attempt" -ge "$max" ]; then
      echo "Still failing after $max attempts: $*" >&2
      return 1
    fi
    echo "  Not ready yet (new accounts take a minute to appear); retrying in 10s ($attempt/$max)..." >&2
    sleep 10
    attempt=$((attempt + 1))
  done
}
