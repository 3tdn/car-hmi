#!/usr/bin/env bash
# Grant the service account access without making private project trees public.
# The deploy script supplies root(), setfacl and runuser.
grant_frontend_access() {
    local frontend_user="$1" frontend_dir="$2" node_bin="$3" target parent

    # Do not follow symlinks out of the frontend project. npm ci installs its
    # dependencies here; the server and public assets need read access only.
    root setfacl -R -P -m "u:${frontend_user}:rX" -- "$frontend_dir" || return $?

    # A private home directory can hide both the project and an nvm Node binary.
    # Add traversal only where needed, preserving access to unrelated files.
    for target in "$frontend_dir" "$node_bin"; do
        parent="$(dirname "$target")"
        while [[ "$parent" != / ]]; do
            if ! root runuser -u "$frontend_user" -- test -x "$parent"; then
                root setfacl -m "u:${frontend_user}:--x" -- "$parent" || return $?
            fi
            parent="$(dirname "$parent")"
        done
    done
    if ! root runuser -u "$frontend_user" -- test -r "$node_bin" ||
       ! root runuser -u "$frontend_user" -- test -x "$node_bin"; then
        root setfacl -m "u:${frontend_user}:r-x" -- "$node_bin" || return $?
    fi
}
