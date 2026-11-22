// The server replaces these two values with its local fixture origins.
const apiEndpoint = API_ENDPOINT;
const cdnOrigin = CDN_ORIGIN;

document.addEventListener('DOMContentLoaded', () => {
  const button = document.createElement('button');
  button.setAttribute('role', 'tab');
  button.textContent = 'Records';
  button.onclick = () => {
    fetch(apiEndpoint, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer CDN_TEST_BEARER',
        'apollographql-client-name': 'SYNTHETIC_DASHBOARD',
        'apollographql-client-version': 'SYNTHETIC_VERSION'
      },
      body: JSON.stringify({query: 'query Status { status }', operationName: 'Status'})
    });

    // Discovery must block the mutation and all unrelated write/API traffic.
    fetch(apiEndpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: 'mutation Save { save }'})
    }).catch(() => {});
    fetch('/write', {method: 'POST'}).catch(() => {});
    fetch(cdnOrigin + '/api/private.js').catch(() => {});
    fetch(cdnOrigin + '/write', {method: 'POST'}).catch(() => {});
  };
  document.body.appendChild(button);
});
