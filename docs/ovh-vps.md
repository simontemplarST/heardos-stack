# OVHcloud as the VPS provider

The installer can order the lighthouse VPS from OVHcloud (US, Canada or Europe accounts). OVH differs from the other
providers in three ways:

- **Monthly billing.** A VPS is bought through the order cart for one month at a time (no commitment). Removing it early
  does not refund the month. Set a default payment method first: the installer checks out with
  `autoPayWithPreferredPaymentMethod`, and an unpaid order stops the install with the payment link.
- **Signed API keys, not a token.** Create an application key, application secret and consumer key on the key page for
  your account's region and paste all three into the installer:

  | Account | Key page |
  |---|---|
  | OVHcloud US | https://api.us.ovhcloud.com/createToken |
  | OVHcloud Canada | https://ca.api.ovh.com/createToken |
  | OVHcloud Europe | https://eu.api.ovh.com/createToken |

  Set validity to *Unlimited* and grant only these rights:

  ```
  GET  /me            GET  /me/order/*     GET  /order/*
  POST /order/cart    POST /order/cart/*
  GET  /vps           GET  /vps/*          POST /vps/*/rebuild
  POST /vps/*/terminate                    POST /vps/*/confirmTermination
  ```

  `heardos-cli secret --import` turns `OVH_ENDPOINT`, `OVH_APP_KEY`, `OVH_APP_SECRET` and `OVH_CONSUMER_KEY` from
  `credentials.env` into the vault entry `OVH_CREDENTIALS`.
- **Termination needs an email.** `destroy` asks OVH to terminate the VPS; OVH then emails the account owner a link.
  Click it, or pass its token to `heardos-cli ovh-confirm <service> <token>`.

How the order runs (`installer/heardos_installer/providers/ovh.py`):

1. Plans and prices come from the public catalog (`/order/catalog/public/vps`). Only the current range is listed, and
   each price includes the cheapest of every *mandatory* add-on. Local Zone plans, for example, require an automated
   backup option ($1.20/month on VPS-2 LZ).
2. Stock comes from `/vps/order/rule/datacenter`; sold-out locations are hidden. Local Zones sell out often.
3. Cart → configure `vps_datacenter` and `vps_os` (Ubuntu 24.04) → mandatory add-ons → assign → checkout.
4. When the new service appears under `/vps`, the installer rebuilds it with its SSH key
   (`/vps/{service}/rebuild`, `publicSshKey`). It then copies the key from the image's `ubuntu` user to root, because the
   kit's VPS steps log in as root.
