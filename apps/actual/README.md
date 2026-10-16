# Actual Budget scripts (run on CT 108)

The sync server is OpenID-only, so the API needs a session token. One lives in
`/root/runtipi/app-data/_user/cronmaster/scripts/actual-to-wallos.env` (ACTUAL_SESSION_TOKEN, filled 2026-10-14).
Run any script here the same way `actual-to-wallos.sh` does — inside the heardos-dash image on the Runtipi network:

    S=/root/runtipi/app-data/_user/cronmaster/scripts; C=/root/runtipi/app-data/_user/cronmaster/data/actual-cache
    scp actual-uncat.mjs root@192.168.10.20:$S/
    ssh root@192.168.10.20 "docker run --rm --network runtipi_tipi_main_network --env-file $S/actual-to-wallos.env \
      -v $S/actual-uncat.mjs:/app/sync/actual-uncat.mjs:ro -v $C:/data/actual-cache -w /app heardos-dash:0.1 node /app/sync/actual-uncat.mjs"

- `actual-uncat.mjs` — read-only: categories, accounts, uncategorized on-budget transactions grouped by payee (JSON after the log noise).
- `actual-apply.mjs [--apply]` — applies a `plan.json` (`sets`: [{id, cat:"Group/Name"}], `transfers`: [{house_id, card, delete_id}]).
  `plan-20261015.json` is the plan that cleared 125 uncategorized transactions on 2026-10-15.

Gotcha: changing a transaction's payee to a transfer payee only sometimes creates the linked counterpart when many
updates run back to back; re-set the payee (plain payee, then transfer payee again, ~1 s apart) for any line whose
`transfer_id` stays empty. Delete the bank-imported "Payment" line on the card side first so there is one linked pair.
