# Cross-host disaster-recovery drill

**Status:** Operator runbook; the repository CI currently validates a fresh restore on one Docker host. This procedure describes the required two-host acceptance drill. Do not mark cross-host recovery as verified until the drill has been executed and its evidence recorded.

## 1. Test topology

Use two disposable Linux hosts with compatible CPU architecture and Docker Engine / Compose v2:

- **Source host A:** running the disposable OpenSigWeave deployment and creating the backup.
- **Destination host B:** clean Docker host with no OpenSigWeave containers or volumes.
- **Transfer:** copy only the encrypted `.age` archive and the age identity through an approved secure channel. Never transfer the private identity through an untrusted share or include it in the archive.

The backup contains the application and PostgreSQL images, so a registry pull is not required during restore. Both hosts must have sufficient disk space for the encrypted archive, extracted staging data, saved images and restored volumes.

## 2. Create and verify on host A

1. Confirm the source deployment is healthy and record its OpenSigWeave version, Compose project name and Alembic revision.
2. Create the backup using the documented `backup_cli.py create` command and a public age recipient.
3. Verify the resulting archive with the corresponding private identity.
4. Record the archive SHA-256, size, timestamp and verification output.
5. Copy the encrypted archive to host B. Keep the private age identity separate and protect it with restrictive permissions.

Example:

```bash
python3 backup_cli.py create \
  --project-dir /opt/opensigweave \
  --output /mnt/transfer/opensigweave-drill.age \
  --recipient "$AGE_RECIPIENT" \
  --identity "$AGE_IDENTITY"

python3 backup_cli.py verify /mnt/transfer/opensigweave-drill.age \
  --identity "$AGE_IDENTITY"

sha256sum /mnt/transfer/opensigweave-drill.age
```

## 3. Restore on host B

1. Install Docker Engine with the Compose v2 plugin, Python and age.
2. Ensure host B has no containers using the chosen Compose project name and none of the expected named volumes.
3. Place the encrypted archive and age identity on host B. Set the identity file mode to `0600`.
4. Run restore into a new directory and a unique Compose project name:

```bash
chmod 0600 /secure/age-identity.txt

python3 backup_cli.py restore /mnt/transfer/opensigweave-drill.age \
  --identity /secure/age-identity.txt \
  --target-dir /opt/opensigweave-drill-restore \
  --project-name opensigweave-drill-restore \
  --health-timeout 300
```

Run the command on host B so that its local Docker daemon owns the restored containers and volumes. Do not restore over the production deployment.

## 4. Acceptance checks

The drill passes only when all checks below are evidenced:

- [ ] The archive verifies on host B using the separately transferred identity.
- [ ] Restore completes using host B's Docker daemon without access to host A's Docker daemon or volumes.
- [ ] PostgreSQL is healthy and reports the same Alembic revision as the backup manifest.
- [ ] Domain, user, group, provider and signature records are present and representative records match host A.
- [ ] Signature files and immutable versions match the archive checksums.
- [ ] Identity-provider configuration files are present and match the archive checksums.
- [ ] The restored application healthcheck succeeds.
- [ ] Login and signature rendering work in an isolated test environment.
- [ ] The restored instance is reachable only through the drill's isolated test route; production DNS and reverse-proxy routing are unchanged.
- [ ] The original host A can be powered off or disconnected during destination verification.

Record the source and destination host identifiers, Docker/Compose versions, archive SHA-256, backup timestamp, restore duration, Alembic revision, test results and any deviations. Do not record passwords, tokens or private key material.

## 5. Failure handling and cleanup

A failed restore deliberately leaves its newly created target directory and volumes for inspection. Do not run broad `docker volume prune` or remove volumes by wildcard. Inspect the exact Compose project and named volumes before manually cleaning up the disposable destination.

Keep the encrypted archive and private identity until the drill evidence has been reviewed. After acceptance, remove only the explicitly identified disposable project, volumes and temporary plaintext files.

## 6. Operational limits

- A successful same-host CI restore is not proof of cross-host recovery.
- Matching architecture and compatible Docker Engine / Compose versions are assumed; cross-architecture restore is not covered.
- The private age identity is a critical recovery dependency. Maintain an independently protected escrow copy and periodically prove it can decrypt a backup.
- A remote NFS/SMB backup mount protects against loss of the application host only when the storage server and credentials are independently available.
- No destructive in-place restore is supported by this runbook.
