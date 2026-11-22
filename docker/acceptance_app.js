// The fixture page supplies the synthetic bearer after its initial login.
// Discovery must retain the observed client and selected-role context privately.
fetch('/graphql', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: window.fixtureBearer,
    'apollographql-client-name': 'SYNTHETIC_DASHBOARD',
    'apollographql-client-version': 'SYNTHETIC_VERSION',
    'x-role-id': 'SYNTHETIC_ROLE'
  },
  body: JSON.stringify({query: '{status}'})
});
