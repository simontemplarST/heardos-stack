# Outbound mail through Resend

There is no mail server in the stack. Authentik is the only sender today (password resets, sign-in codes, invitations,
account activation, notifications); other apps can be pointed at the same relay when they need it.

## Why a relay

The VPS provider blocks outbound port 25 and the home connection sits behind carrier-grade NAT with no reverse DNS, so
direct delivery from either host would be rejected or spam-foldered. Port 587 is open from both. Cloudflare has no
outbound relay product.

## Set-up

1. Create a Resend account, add the domain, and let its Cloudflare integration publish the records (DKIM at
   `resend._domainkey`, return-path CNAMEs `send.` and `rsend.`). Click Verify.
2. Put the API key in `credentials.env` as `RESEND_API_KEY`. A send-only key is enough for Authentik; a full-access key
   as `RESEND_ADMIN_KEY` lets `mail/resend-dns.sh` manage and verify the domain from the API.
3. Authentik's override (`host/runtipi-overrides/authentik-1.docker-compose.yml`) sets
   `AUTHENTIK_EMAIL__HOST=smtp.resend.com`, port 587, TLS on, username `resend`, password = the key, From
   `HeardOS <authentik@<domain>>`, on both the server and the worker. Restart Authentik.
4. Test: `mail/resend-dns.sh` sends a message; or start the recovery flow for an account and watch the worker log for
   `email_sent`.

## DNS posture

- Apex SPF stays `v=spf1 -all`: nothing sends from the apex envelope. Resend's return path is a subdomain with its own
  SPF.
- DKIM is signed with the apex domain, which satisfies strict DKIM alignment.
- DMARC is `p=reject; adkim=s; aspf=r; rua=mailto:<operator>`. SPF alignment is relaxed because the return path is a
  subdomain; strict would make DMARC depend on DKIM alone. Reports to an address on another domain need that domain to
  publish `<thisdomain>._report._dmarc.<otherdomain> TXT "v=DMARC1"`.
- mail-tester.com scored a message 10/10 with this configuration.

## Reputation, for a low-volume sender

Keep the From address stable, get the first recipients to open the mail and mark it not spam, keep bounces at zero
(every account needs a real address), and read the DMARC reports for the first month. Resend's API is behind
Cloudflare and rejects Python's default user agent; send a browser-like one.
