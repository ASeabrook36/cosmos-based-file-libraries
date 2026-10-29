// Apply to the dedicated shared Logic Apps Standard host before enabling its Request trigger.
// This authenticates the workflow bridge only. Hosted FastAPI auth remains Phase 5.
param hostName string
param tenantId string
param workflowAppClientId string
param workflowAudience string
param workerClientId string
param workerPrincipalId string

resource host 'Microsoft.Web/sites@2023-12-01' existing = { name: hostName }
resource auth 'Microsoft.Web/sites/config@2022-09-01' = {
  parent: host
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    httpSettings: { requireHttps: true }
    globalValidation: { requireAuthentication: true, unauthenticatedClientAction: 'Return401' }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: workflowAppClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [workflowAudience]
          defaultAuthorizationPolicy: {
            allowedApplications: [workerClientId]
            allowedPrincipals: { identities: [workerPrincipalId] }
          }
        }
      }
    }
  }
}
