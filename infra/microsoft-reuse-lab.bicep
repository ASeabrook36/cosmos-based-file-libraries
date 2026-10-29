// Opt-in extraction compatibility lab only. Deploy into a fresh, dedicated group.
param location string = resourceGroup().location
param accountName string
param operatorPrincipalId string

resource extraction 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: accountName
  location: location
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  tags: {
    purpose: 'bounded-microsoft-reuse-experiment'
  }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

var cognitiveUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(extraction.id, operatorPrincipalId, cognitiveUser)
  scope: extraction
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    roleDefinitionId: cognitiveUser
  }
}

// Redirect deployment outputs to PRIVATE runtime settings, never a checked-in file.
output endpoint string = extraction.properties.endpoint
