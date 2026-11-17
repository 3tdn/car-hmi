#!/usr/bin/env bash

frontend_port_pids() {
    local port="$1" output pid
    local -a pids=()

    if command -v fuser >/dev/null; then
        output="$(root fuser -n tcp "$port" 2>/dev/null || true)"
        if [[ -n "$output" ]]; then
            read -r -a pids <<< "${output//$'\n'/ }"
        fi
    elif command -v ss >/dev/null; then
        output="$(root ss -H -ltnp "sport = :$port" 2>/dev/null)" || return $?
        while [[ "$output" =~ pid=([0-9]+) ]]; do
            pid="${BASH_REMATCH[1]}"
            if [[ " ${pids[*]} " != *" $pid "* ]]; then
                pids+=("$pid")
            fi
            output="${output#*"pid=$pid"}"
        done
    else
        warn 'Cannot inspect frontend port: install psmisc (fuser) or iproute2 (ss).'
        return 1
    fi

    if ((${#pids[@]})); then
        printf '%s\n' "${pids[@]}"
    fi
}

frontend_process_matches() {
    local pid="$1" frontend_dir="$2" cwd arg entrypoint expected
    local -a args=()
    cwd="$(root readlink -f "/proc/$pid/cwd")" || return 1
    [[ "$cwd" == "$frontend_dir" ]] || return 1
    # Keep argv boundaries: frontend paths can contain spaces, and npm invokes
    # Vite through node_modules/.bin/vite rather than its resolved script path.
    while IFS= read -r -d '' arg; do
        args+=("$arg")
    done < <(root cat "/proc/$pid/cmdline")
    ((${#args[@]} >= 3)) && [[ "${args[2]}" == preview ]] || return 1
    entrypoint="${args[1]}"
    [[ "$entrypoint" = /* ]] || entrypoint="$cwd/$entrypoint"
    entrypoint="$(root readlink -f "$entrypoint")" || return 1
    expected="$(root readlink -f "$frontend_dir/node_modules/vite/bin/vite.js")" || return 1
    [[ "$entrypoint" == "$expected" ]]
}

cleanup_frontend_port() {
    local port="$1" frontend_dir="$2" pid attempt output
    local -a pids=() remaining=()
    output="$(frontend_port_pids "$port")" || return $?
    if [[ -n "$output" ]]; then
        read -r -a pids <<< "${output//$'\n'/ }"
    fi
    ((${#pids[@]})) || return 0

    for pid in "${pids[@]}"; do
        if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! frontend_process_matches "$pid" "$frontend_dir"; then
            warn "Port $port is occupied by a process that is not this frontend; refusing to terminate it."
            return 1
        fi
    done

    log "Stopping stale frontend listener(s) on port $port: ${pids[*]}"
    for pid in "${pids[@]}"; do
        root kill -TERM "$pid" || return $?
    done

    for attempt in 1 2 3 4 5; do
        output="$(frontend_port_pids "$port")" || return $?
        remaining=()
        if [[ -n "$output" ]]; then
            read -r -a remaining <<< "${output//$'\n'/ }"
        fi
        ((${#remaining[@]} == 0)) && return 0
        sleep 1
    done

    for pid in "${remaining[@]}"; do
        if frontend_process_matches "$pid" "$frontend_dir"; then
            root kill -KILL "$pid" || return $?
        else
            warn "Port $port is still occupied by a process that is not this frontend."
            return 1
        fi
    done
    output="$(frontend_port_pids "$port")" || return $?
    remaining=()
    if [[ -n "$output" ]]; then
        read -r -a remaining <<< "${output//$'\n'/ }"
    fi
    if ((${#remaining[@]})); then
        warn "Frontend listener did not release port $port after termination."
        return 1
    fi
}
