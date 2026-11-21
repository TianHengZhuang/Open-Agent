import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export interface NetworkGuardOptions {
  allowedSchemes?: string[];
  blockedDomains?: string[];
  maxRedirects?: number;
}

const BLOCKED_IPV4: Array<[string, number]> = [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
];

function ipv4ToInt(address: string): number {
  return (
    address.split(".").reduce((acc, part) => (acc << 8) + Number(part), 0) >>> 0
  );
}

function inIpv4Range(
  address: string,
  network: string,
  prefix: number,
): boolean {
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (ipv4ToInt(address) & mask) === (ipv4ToInt(network) & mask);
}

function isBlockedIpv6(address: string): boolean {
  const normalized = address.toLowerCase().split("%")[0];
  if (normalized === "::" || normalized === "::1") return true;
  // IPv4-mapped/embedded addresses (::ffff:127.0.0.1).
  const mapped = normalized.match(/::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mapped) return isBlockedAddress(mapped[1]);
  // Unique local (fc00::/7) and link-local (fe80::/10).
  return (
    /^f[cd][0-9a-f]{2}:/.test(normalized) ||
    /^fe[89ab][0-9a-f]:/.test(normalized)
  );
}

export function isBlockedAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) {
    return BLOCKED_IPV4.some(([network, prefix]) =>
      inIpv4Range(address, network, prefix),
    );
  }
  if (family === 6) return isBlockedIpv6(address);
  return true;
}

function isBlockedHostname(
  hostname: string,
  blockedDomains: string[],
): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  return blockedDomains.some(
    (domain) =>
      host === domain.toLowerCase() ||
      host.endsWith(`.${domain.toLowerCase()}`),
  );
}

/** Validate a URL and every resolved address before any request is made. */
export async function assertPublicHttpUrl(
  rawUrl: string | URL,
  options: NetworkGuardOptions = {},
): Promise<URL> {
  const url = rawUrl instanceof URL ? rawUrl : new URL(rawUrl);
  const allowedSchemes = options.allowedSchemes ?? ["http", "https"];
  if (!allowedSchemes.includes(url.protocol.replace(":", ""))) {
    throw new Error(`Scheme ${url.protocol} not allowed`);
  }
  if (url.username || url.password) {
    throw new Error("URLs with embedded credentials are not allowed");
  }
  if (!url.hostname) throw new Error("URL host is required");

  const blockedDomains = options.blockedDomains ?? [];
  if (isBlockedHostname(url.hostname, blockedDomains)) {
    throw new Error(`Domain ${url.hostname} is blocked`);
  }

  const literalFamily = isIP(url.hostname);
  if (literalFamily) {
    if (isBlockedAddress(url.hostname)) {
      throw new Error(`Address ${url.hostname} is blocked`);
    }
    return url;
  }

  let addresses: Array<{ address: string }>;
  try {
    addresses = await lookup(url.hostname, { all: true, verbatim: true });
  } catch {
    throw new Error(`Unable to resolve ${url.hostname}`);
  }
  if (addresses.length === 0) {
    throw new Error(`Unable to resolve ${url.hostname}`);
  }
  for (const { address } of addresses) {
    if (isBlockedAddress(address)) {
      throw new Error(
        `Host ${url.hostname} resolves to blocked address ${address}`,
      );
    }
  }
  return url;
}

/**
 * Fetch with manual redirect handling so every hop is re-validated. Native
 * fetch always validates TLS certificates, so certificate checking stays on.
 */
export async function safeFetch(
  rawUrl: string,
  init: RequestInit,
  options: NetworkGuardOptions = {},
): Promise<Response> {
  const maxRedirects = Math.max(0, Math.min(options.maxRedirects ?? 5, 5));
  let url = await assertPublicHttpUrl(rawUrl, options);

  for (let hop = 0; hop <= maxRedirects; hop++) {
    const response = await fetch(url, { ...init, redirect: "manual" });
    const isRedirect = response.status >= 300 && response.status < 400;
    const location = response.headers.get("location");
    if (!isRedirect || !location) return response;
    if (hop === maxRedirects) {
      throw new Error(`Too many redirects (max ${maxRedirects})`);
    }
    url = await assertPublicHttpUrl(new URL(location, url), options);
  }

  throw new Error(`Too many redirects (max ${maxRedirects})`);
}
