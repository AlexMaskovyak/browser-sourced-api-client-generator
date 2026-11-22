"""Issue a two-request job while another process competes for the same profile."""

import sys
import time

import httpx
from website_client.authenticated_http import profile_dispatch, SDKTransport


def main() -> None:
    endpoint, worker = sys.argv[1:]
    with profile_dispatch("neutral", endpoint) as dispatch:
        with httpx.Client(transport=SDKTransport(dispatch), trust_env=False) as client:
            for _request_number in range(2):
                response = client.get(endpoint + "/rotate?worker=" + worker)
                assert response.status_code == 200, response.text
                # Leave time for the competing process to attempt the lock.
                time.sleep(0.15)


if __name__ == "__main__":
    main()
