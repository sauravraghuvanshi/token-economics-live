#Requires -Version 7.4
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [guid]$SubscriptionId,
    [Parameter(Mandatory)] [string]$ResourceGroup,
    [Parameter(Mandatory)] [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9-]+$')] [string]$WebAppName
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$account = az account show --query '{id:id, tenant:tenantId, cloud:environmentName}' --output json | ConvertFrom-Json
if ($account.id -ne "$SubscriptionId" -or $account.cloud -ne 'AzureCloud') {
    throw "Select the approved AzureCloud subscription first: az account set --subscription $SubscriptionId"
}
az account get-access-token --subscription $SubscriptionId --resource-type arm --output none
az account get-access-token --subscription $SubscriptionId --resource-type ms-graph --output none

$groupArgs = @('--subscription', "$SubscriptionId", '--resource-group', $ResourceGroup, '--only-show-errors')
$siteArgs = $groupArgs + @('--name', $WebAppName)
$site = az webapp show @siteArgs --query '{id:id, host:defaultHostName, location:location, httpsOnly:httpsOnly, identity:identity}' `
    --output json | ConvertFrom-Json -AsHashtable
if (-not $site.id -or -not $site.httpsOnly -or $site.host -notlike '*.azurewebsites.net' -or -not $site.identity.principalId) {
    throw 'Expected an HTTPS App Service with its existing system-assigned identity.'
}
$authUrl = "https://management.azure.com$($site.id)/config/authsettingsV2/list?api-version=2024-04-01"
$authArgs = @('--method', 'get', '--url', $authUrl, '--subscription', "$SubscriptionId", '--query', 'properties',
              '--only-show-errors', '--output', 'json')
$auth = az rest @authArgs | ConvertFrom-Json
if ($auth.platform.enabled -or $auth.platform.configFilePath -or $auth.identityProviders) {
    throw 'App Service already has authentication configured. Review it before making any changes.'
}
$siteUrl = "https://$($site.host)"
$ready = Invoke-RestMethod -Uri "$siteUrl/readyz" -TimeoutSec 15 -MaximumRedirection 0 `
    -SkipHttpErrorCheck -StatusCodeVariable readyStatus
if ($readyStatus -ne 200 -or $ready.mode -ne 'live' -or -not $ready.workload_version) {
    throw "Deploy the Live /readyz release and wait for it to be ready (HTTP $readyStatus). No changes made."
}

$name = "$WebAppName-sign-in"
$callback = "$siteUrl/.auth/login/aad/callback"
$registrations = @(az ad app list --display-name $name --query "[?displayName == '$name']" --output json | ConvertFrom-Json)
if ($registrations.Count -gt 1) {
    throw "Multiple Entra app registrations named $name. Resolve the duplicate before continuing."
}
if ($registrations.Count -eq 1) {
    $registration = $registrations[0]
} else {
    $registration = az ad app create --display-name $name --sign-in-audience AzureADMyOrg `
        --web-redirect-uris $callback --enable-id-token-issuance true --output json | ConvertFrom-Json
}
if ($registration.signInAudience -ne 'AzureADMyOrg' -or $registration.web.redirectUris.Count -ne 1 `
    -or $registration.web.redirectUris[0] -ne $callback `
    -or -not $registration.web.implicitGrantSettings.enableIdTokenIssuance) {
    throw "The registration $name doesn't match the expected single-tenant app and callback. No site settings changed."
}
if (-not (az ad sp list --filter "appId eq '$($registration.appId)'" --query '[].id' --output tsv)) {
    az ad sp create --id $registration.appId --output none
}

$identity = az identity list @groupArgs --query "[?name == '$name'] | [0]" --output json | ConvertFrom-Json
if (-not $identity) {
    $identity = az identity create @groupArgs --name $name --location $site.location --output json | ConvertFrom-Json
}
if ($identity.tenantId -ne $account.tenant -or -not $identity.clientId -or -not $identity.principalId) {
    throw 'The sign-in managed identity must be ready in the subscription tenant.'
}

$credential = @{
    name = 'app-service-sign-in'
    issuer = "https://login.microsoftonline.com/$($account.tenant)/v2.0"
    subject = $identity.principalId
    audiences = @('api://AzureADTokenExchange')
}
$existing = az ad app federated-credential list --id $registration.id `
    --query "[?name == 'app-service-sign-in'] | [0]" --output json | ConvertFrom-Json
if (-not $existing) {
    $credentialFile = New-TemporaryFile
    try {
        $credential | ConvertTo-Json | Set-Content -LiteralPath $credentialFile.FullName -Encoding utf8
        az ad app federated-credential create --id $registration.id --parameters $credentialFile.FullName --output none
    } finally {
        Remove-Item -LiteralPath $credentialFile.FullName
    }
} elseif ($existing.issuer -cne $credential.issuer -or $existing.subject -cne $credential.subject `
          -or $existing.audiences.Count -ne 1 -or $existing.audiences[0] -cne $credential.audiences[0]) {
    throw 'The existing federated credential does not match the dedicated managed identity.'
}

$attached = $site.identity.userAssignedIdentities
if (-not $attached -or -not $attached.Contains($identity.id)) {
    $identities = @('[system]') + @($attached.Keys | Where-Object { $_ }) + @($identity.id)
    az webapp identity assign @siteArgs --identities @identities --output none
}
$identities = az webapp identity show @siteArgs --output json | ConvertFrom-Json -AsHashtable
if ($identities.principalId -ne $site.identity.principalId -or
    -not $identities.userAssignedIdentities -or -not $identities.userAssignedIdentities.Contains($identity.id)) {
    throw 'The expected runtime and sign-in identities are not attached. Sign-in was not enabled.'
}
foreach ($id in $attached.Keys) {
    if (-not $identities.userAssignedIdentities.Contains($id)) {
        throw 'An existing managed identity is no longer attached. Sign-in was not enabled.'
    }
}
az webapp config appsettings set @siteArgs --slot-settings `
    "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID=$($identity.clientId)" `
    "WEBSITE_AUTH_AAD_ALLOWED_TENANTS=$($account.tenant)" --output none

az deployment group create @groupArgs --name $name `
    --template-file (Join-Path $PSScriptRoot '..\infra\demo-sign-in.bicep') `
    --parameters "webAppName=$WebAppName" "loginClientId=$($registration.appId)" `
    --confirm-with-what-if --output none

$auth = az rest @authArgs | ConvertFrom-Json
if (-not $auth.platform.enabled -or -not $auth.globalValidation.requireAuthentication -or
    $auth.identityProviders.azureActiveDirectory.registration.clientId -ne $registration.appId) {
    throw 'The expected authentication gate was not enabled.'
}
Write-Host "Microsoft sign-in is configured for $siteUrl. Complete a sign-in in an authorized browser to verify access."
