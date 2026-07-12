#!/usr/bin/env bash
set -euo pipefail

input="$(cat)"

case "$input" in
  *"UtilAcceptVsock"*|*"UtilBindVsock"*|*"socket failed"*|*"failed 110"*)
    printf '%s\n' 'classification=interop_transport' 'action=stop_after_one_health_recheck_recover_wsl_from_windows_at_safe_boundary'
    ;;
  *"cannot execute binary file"*|*"Exec format error"*)
    printf '%s\n' 'classification=interop_binary_dispatch' 'action=stop_windows_executables_do_not_change_powershell_source'
    ;;
  *"An empty pipe element is not allowed"*|*"ParserError"*|*"ParseException"*)
    printf '%s\n' 'classification=powershell_parse' 'action=compile_whole_source_with_runner_fix_source_before_execution'
    ;;
  *"The term '=' is not recognized"*|*"/bin/bash.ProcessName"*|*"Missing an argument for parameter"*)
    printf '%s\n' 'classification=outer_shell_expansion' 'action=use_single_quoted_heredoc_and_deterministic_runner'
    ;;
  *"HOST_MISMATCH"*|*'"error_kind":"host_mismatch"'*)
    printf '%s\n' 'classification=host_mismatch' 'action=stop_verify_target_alias_and_identity'
    ;;
  *"timed_out"*|*'"error_kind":"timeout"'*)
    printf '%s\n' 'classification=timeout' 'action=stop_check_exact_pid_and_post_state_before_retry'
    ;;
  *"Access to a CIM resource was not available"*|*"Access is denied"*|*"UnauthorizedAccess"*)
    printf '%s\n' 'classification=permission_or_uac' 'action=stop_name_required_elevation_and_use_real_visible_approval_path'
    ;;
  *"Avast"*|*"Threat secured"*|*"security alert"*)
    printf '%s\n' 'classification=security_alert' 'action=stop_do_not_disable_exclude_obfuscate_or_bypass'
    ;;
  *"missing_result_envelope"*)
    printf '%s\n' 'classification=runner_contract_failure' 'action=stop_runner_cannot_claim_success_without_envelope'
    ;;
  *)
    printf '%s\n' 'classification=unknown' 'action=inspect_exact_stage_shell_host_exit_and_logs_before_retry'
    ;;
esac
