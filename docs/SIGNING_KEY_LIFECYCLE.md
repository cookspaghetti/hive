# Evidence Signing-Key Lifecycle

HIVE signs evidence with an unencrypted RSA private key stored inside the
protected project `secrets/` directory. The private key is a high-value custody
asset. File permissions, host access, backups, and operator procedure are part
of the trust boundary; a valid signature alone does not prove who controlled
the key.

## Public identity

The Security workspace displays:

- algorithm and RSA key size;
- SHA-256 fingerprint of the canonical SubjectPublicKeyInfo public key;
- whether the companion public-key file is present and matches; and
- the key file's last-modified time.

The fingerprint contains no private material. New evidence-package manifests
also record it, while every portable package embeds the public key needed for
offline verification. Previously sealed packages therefore remain verifiable
after rotation.

## Creating the first key

1. Restrict the HIVE panel and host to the responsible operator.
2. Open **Security**, confirm the path remains inside the project, and select
   **Create signing key**.
3. Record the displayed fingerprint in the project custody log.
4. Back up the private key only under the approved encrypted secret-handling
   procedure. Do not place it in Git, evidence packages, screenshots, or UAT
   attachments.

HIVE refuses to overwrite an existing file, including an invalid key file.

## Planned rotation

1. Stop and seal or checkpoint every active takeover, then stop the agent.
2. Open **Security** and compare the displayed current fingerprint with the
   custody record.
3. Enter a short non-sensitive reason and select **Rotate signing key**.
4. Review the confirmation. The API also requires the displayed fingerprint to
   still match, preventing a stale panel from rotating a different key.
5. HIVE creates a uniquely named 2,048-bit RSA keypair, validates it, updates
   `HIVE_SIGNING_KEY_PATH`, retains the previous key, and records previous/new
   paths and fingerprints in the permanent audit ledger.
6. Start the agent so new sessions use the new key. Create a synthetic evidence
   package, verify it offline, and record the package ID and new fingerprint.

Rotation is blocked while the agent is running and concurrent rotations are
rejected. HIVE never overwrites or deletes the previous key during rotation.

## Previous-key retention and retirement

Keep an old public key for at least as long as any evidence signed by it must be
verified. Portable evidence packages already contain that public key. Retain or
retire the old private key according to the supervisor-approved evidence and
legal-hold policy; HIVE does not automate private-key deletion.

Before any manual retirement:

1. identify all evidence and manifests bearing the fingerprint;
2. verify representative packages independently;
3. confirm retention, appeal, investigation, and legal-hold requirements;
4. preserve the public key and fingerprint custody record; and
5. record the authorised disposal method and result outside HIVE.

## Suspected compromise

Stop the agent, preserve logs and host evidence, record the suspected exposure
time, and rotate from a trusted host after containment. Treat signatures made
during the uncertain interval as requiring additional custody evidence.
Rotation cannot retroactively restore trust to already disputed signatures.

## Prototype limitations

- Private keys are file-based and unencrypted at rest; HIVE relies on directory
  permissions and host controls rather than an HSM, TPM, or managed KMS.
- There is no certificate authority, revocation service, automatic rotation
  schedule, legal-hold engine, or secure-deletion implementation.
- The rotation audit record establishes what this HIVE instance observed; an
  independent timestamp/custody authority would provide stronger assurance.

