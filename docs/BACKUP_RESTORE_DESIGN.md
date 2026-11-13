# Backup and disaster recovery design

**Status:** Versioned archive primitives, age file/archive encryption, Docker Compose full-backup creation (including saved container images), encrypted archive verification and initial fresh-deployment restore are implemented. Cross-host restore testing, destructive restore and UI exports are not implemented.
**Scope:** Standalone OpenSigWeave deployment. This design does not configure or modify Mailcow.

## 1. Goals and guarantees

OpenSigWeave needs two backup entry points with different scopes:

- **Administrator UI:** selectable, component-level exports for operational use.
- **Console:** one complete, portable disaster-recovery backup. Partial console backups are intentionally not supported.

A complete backup must be restorable on a different Linux host with Docker Compose, without depending on the original Docker volumes or host paths. Restore must preserve signature content and history, domain configuration, identity-provider metadata and credentials, application configuration, and all database state required by the application.

The implementation must never describe a backup as successful until the archive has been finalized, encrypted, and verified.

## 2. Data inventory

| Component | Source of truth | Full backup | Selective UI export |
|---|---|---:|---:|
| PostgreSQL records and migrations | PostgreSQL volume | Yes | Database component |
| Current signatures and immutable versions | opensigweave-signatures volume | Yes | Signature component |
| Identity-provider secret files | opensigweave-idp-data volume (/app/.idp) | Yes | Identity-provider component |
| Application environment and independent secrets | Host .env | Yes | Configuration component, secrets excluded by default |
| Compose definition and deployment metadata | Host compose.yaml and manifest | Yes | Configuration component |
| Application image identity/version | Image digest and release metadata | Yes | Manifest metadata |

Both PostgreSQL and the signature volume are mandatory for a usable full restore. The IDP volume and host environment file are mandatory to restore managed authentication and the configured application secrets.

Do not include Docker socket contents, host SSH keys, unrelated volumes, logs containing credentials, or arbitrary host directories.

## 3. Archive format

Use a versioned archive format. A full backup contains:

    opensigweave-backup-<UTC timestamp>/
    ├── manifest.json
    ├── database/
    │   └── opensigweave.dump
    ├── data/
    │   ├── signatures.tar.gz
    │   └── idp-config.tar.gz
    └── deployment/
        ├── compose.yaml
        └── env.enc

The final distributable artifact is encrypted with age. The encryption recipient is configured separately from the application secrets; the corresponding private identity must be stored offline or in a separate secrets vault. Never put the age private identity in the backup or the same server's .env.

The manifest records at least:

- Backup format version and OpenSigWeave version/image digest.
- UTC creation time and database engine/version.
- Included component names and byte sizes.
- SHA-256 checksums for every payload file.
- Migration revision at backup time.
- Whether the environment file is present and encrypted.

Checksums are verified after decryption and before any restore action. A checksum is an integrity check, not a substitute for authenticated encryption.

## 4. Consistency and creation flow

The full console operation is all-or-nothing:

1. Validate Docker Compose project, required services, backup destination, encryption recipient, and free disk space.
2. Create a private temporary staging directory with restrictive permissions.
3. Stop the application service to prevent signature/IDP writes while the snapshot is taken. Keep PostgreSQL running.
4. Export PostgreSQL using a PostgreSQL-native custom-format dump.
5. Archive the signature and IDP volumes from the stopped application container's mounted volumes.
6. Copy the Compose definition and encrypt the host .env as a separate payload.
7. Write the manifest and SHA-256 checksums.
8. Build the archive, encrypt it with age, then decrypt to a temporary verification stream and validate the archive/checksums.
9. Atomically publish the encrypted artifact only after verification.
10. Restart the application service and remove temporary plaintext material, including on failure.

If any step fails, the command returns a non-zero exit code, does not publish a partial backup, removes temporary plaintext files where possible, and attempts to restart the application service. It must clearly report if restart recovery also fails.

A backup must not silently proceed without the IDP volume or encryption. A deliberate UI export may omit components, but its manifest must state exactly what is absent and it must not be labelled a disaster-recovery backup.

## 5. Restore flow and safety

Restore is a destructive administrative operation and must be a separate, explicit command:

    python backup_cli.py create --project-dir /opt/opensigweave --output /backup/opensigweave-<timestamp>.age --recipient <age-recipient> --identity /media/offline/age-identity.txt
    python backup_cli.py verify /backup/opensigweave-<timestamp>.age --identity /media/offline/age-identity.txt
    python backup_cli.py decrypt /backup/opensigweave-<timestamp>.age --identity /media/offline/age-identity.txt --output /tmp/opensigweave.tar.gz

    # Lower-level packaging of an already prepared staging directory:
    python backup_cli.py package --staging <prepared-directory> --output <backup.age> --recipient <age-recipient>

The `create` command validates the Compose project, stops the application service, exports PostgreSQL, copies the signature and IDP volumes from the stopped application container, encrypts the host `.env` as a separate payload, creates the archive, and decrypt-verifies the encrypted result. The age identity is required for that final verification and should be supplied from removable/off-host storage. The lower-level `package` command remains available for an already prepared staging directory.

The restore command must:

1. Require an explicit confirmation flag for destructive replacement (for example, --confirm) and print the target Compose project and volumes.
2. Decrypt and verify the complete archive before stopping or deleting any target data.
3. Reject unsupported format versions, missing mandatory components, path traversal entries, symlinks, unexpected file types, oversized payloads, and checksum mismatches.
4. Require a compatible or explicitly supported target OpenSigWeave version.
5. Stop the application and database services and create a safety snapshot of any existing target data before replacement.
6. Restore PostgreSQL into an empty target database/volume and restore the signature and IDP data into their respective volumes with restrictive ownership and permissions.
7. Restore the host deployment configuration only through an explicit operator-controlled step; never overwrite an existing .env silently.
8. Start the stack, wait for health checks, verify Alembic revision and signature-file integrity, and report the result.
9. Keep the pre-restore safety snapshot until the operator explicitly removes it.

A failed restore must not automatically delete the safety snapshot. Recovery instructions must be printed and logged without exposing secret values.

## 6. UI component exports

The administrator UI may offer these component choices:

- Database
- Signatures and immutable versions
- Identity-provider configuration and credentials
- Deployment configuration

UI exports must show the exact included components before creation. Secrets are excluded from a configuration-only export by default. Including IDP credentials or application secrets requires a clear warning and encrypted output. A partial export is for migration or inspection, not a complete disaster-recovery guarantee.

The UI must enforce administrator authorization, CSRF protection, rate/size limits, and an explicit download action. Backup creation should run as a bounded background job rather than holding an HTTP request open. Job state must not expose secret contents.

## 7. Key handling and retention

- Encryption is mandatory for full backups and any export containing credentials.
- The age recipient is public and may be configured on the host; the private identity must be kept separately.
- Losing the private identity makes encrypted backups unrecoverable. Warn during setup and document key escrow.
- Never log passwords, tokens, decrypted .env contents, or private identities.
- Temporary plaintext files and directories use restrictive permissions and are removed on completion/failure.
- Retention is configurable by count/age at the storage destination; automatic pruning must never delete the only known-good backup.
- Backups should be copied off-host. A backup stored only on the same Docker host does not protect against host loss.

## 8. Verification and acceptance tests

The feature is not complete until automated tests cover:

- Full backup includes database, signatures, IDP credentials, encrypted environment and deployment metadata.
- Docker Compose preflight rejects missing services, stopped containers and missing signature/IDP volume mounts.
- The application service is restarted after successful and failed snapshot attempts, and a restart failure is reported distinctly.
- Missing volume, invalid recipient, insufficient disk space, failed dump, failed archive, failed encryption and failed verification all fail closed.
- Application service is restarted after both successful and failed backup attempts.
- Archive traversal, symlinks, unexpected file types, unsupported versions and tampered checksums are rejected before restore.
- Restore to a fresh Compose project on a different host reproduces database records, signature versions and hashes, IDP provider files and application configuration.
- Existing target data is protected by a safety snapshot before destructive restore.
- UI component selection produces an accurate manifest and never claims a partial export is a full backup.
- No secret values appear in logs, API responses, manifests, or test failure output.

A documented restore drill against a clean, disposable host is a release requirement. Merely testing that an archive can be created is insufficient.

## 9. Delivery order

1. ~~Complete Docker-aware snapshot orchestration around the implemented versioned archive, age encryption and verification primitives.~~
2. ~~Implement safe restore into a fresh deployment.~~ Add cross-host restore drills, deeper post-restore validation and, only later, destructive restore with safety snapshots.
3. Add automated cross-host restore tests and a documented restore drill.
4. Add selective administrator UI exports as a separate layer over the same backup components.
5. Add retention and off-host destination support after the basic restore path is proven.

Until these steps are complete, the Compose volumes are persistent storage, not a backup solution.
