param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RemoteCommand)
$ErrorActionPreference = 'Stop'
$noaProjectRoot = Split-Path -Parent $PSScriptRoot
$noaPrivateKey = Join-Path $noaProjectRoot '.docker/noatech-admin-key'
$noaKnownHosts = Join-Path $noaProjectRoot '.docker/noatech-known-hosts'
if (!(Test-Path -LiteralPath $noaPrivateKey) -or !(Test-Path -LiteralPath $noaKnownHosts)) {
    throw 'The dedicated owner SSH key and pinned host record are required.'
}
& ssh -i $noaPrivateKey -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$noaKnownHosts" root@94.184.47.177 @RemoteCommand
exit $LASTEXITCODE
