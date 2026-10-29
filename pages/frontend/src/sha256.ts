import {sha256} from '@noble/hashes/sha2.js';

// LAN HTTP pages do not expose SubtleCrypto. Hash the same bytes with the
// bundled, pinned implementation there; never skip integrity verification.
export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  try {
    const subtle = globalThis.crypto?.subtle;
    const digest = subtle && typeof subtle.digest === 'function'
      ? new Uint8Array(await subtle.digest('SHA-256', bytes as BufferSource))
      : sha256(bytes);
    return Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
  } catch {
    // A present but failing native implementation remains a hard failure.
    throw Error('asset_digest_unavailable');
  }
}
