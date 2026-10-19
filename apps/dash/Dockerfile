# HeardOS desktop — one small Node container: the web desktop (public/), the app catalogue (apps.json, filtered per person
# from the Authentik gate's headers) and /api/summary (Mealie, Actual Budget, Homebox) for the This week app and widgets.
# node:22-slim, pinned to the multi-arch index digest (looked up 2026-10-19). To update: skopeo inspect --raw
# docker://docker.io/library/node:22-slim | sha256sum, then rebuild and test.
FROM docker.io/library/node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS deps
# better-sqlite3 13 (via @actual-app/api) ships no install script/prebuild, so npm compiles it: build tools here only
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# exact dependency tree from the committed lockfile (regenerate: npm install --package-lock-only)
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

FROM docker.io/library/node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ENV NODE_ENV=production TZ=America/Chicago
WORKDIR /app
COPY package.json package-lock.json ./
COPY --from=deps /app/node_modules ./node_modules
COPY --chown=node:node server.js apps.json fetch-icons.mjs ./
COPY --chown=node:node public ./public
RUN node fetch-icons.mjs && chown -R node:node public/icons
# no recursive chown of node_modules: on overlayfs that copies up every file and takes minutes
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
