"""PostgreSQL-only concurrency regression for signature-group selection."""
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import sessionmaker

from core.group_selection_repository import select_and_record_group_signature
from models import (DomainDB, GroupSelectionStateDB, SignatureDB, SignatureGroupDB, SignatureGroupMemberDB)


DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_URL = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_URL, "requires the PostgreSQL CI database")
class PostgreSQLSelectionConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_size=12, max_overflow=0)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_catalog_share_lock_does_not_block_an_unrelated_sender(self):
        """Concurrent catalog readers must not force every sender to queue."""
        principal = f"oidc:{uuid.uuid4().hex}"
        group_id = uuid.uuid4().hex
        signature_id = uuid.uuid4().hex
        with self.Session.begin() as session:
            domain = DomainDB(domain_name=f"{uuid.uuid4().hex}.example", is_active=True)
            session.add(domain)
            session.flush()
            domain_id = domain.id
            session.add(SignatureGroupDB(
                id=group_id, name="Unrelated principal", slug=f"cc-{group_id[:12]}",
                is_active=True,
            ))
            session.add(SignatureDB(
                id=signature_id, name=f"Signature-{signature_id}", scope="GLOBAL",
                mode="UNUSED", is_active=True,
            ))
            session.add(SignatureGroupMemberDB(
                group_id=group_id, signature_id=signature_id, weight=1, is_enabled=True,
            ))
        try:
            # A concurrent reader holds FOR SHARE. The previous group
            # candidate FOR UPDATE blocked here; the new read does not.
            # A real administrator's FOR UPDATE still legitimately blocks
            # foreign-key KEY SHARE checks on the selected signature.
            with self.Session() as editor:
                editor.execute(
                    select(SignatureDB).where(SignatureDB.id == signature_id)
                    .with_for_update(read=True)
                ).scalar_one()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    def select_other_sender():
                        with self.Session.begin() as session:
                            # Independent protection against an indefinitely blocked CI job.
                            session.execute(
                                text("SELECT set_config('lock_timeout', '1500ms', true)")
                            )
                            return select_and_record_group_signature(
                                session, principal, group_id, domain_id
                            ).id
                    result = executor.submit(select_other_sender).result(timeout=8)
                self.assertEqual(result, signature_id)
                editor.rollback()
        finally:
            with self.Session.begin() as session:
                session.execute(delete(GroupSelectionStateDB).where(
                    GroupSelectionStateDB.principal_id == principal,
                    GroupSelectionStateDB.group_id == group_id,
                ))
                session.execute(delete(SignatureGroupMemberDB).where(
                    SignatureGroupMemberDB.group_id == group_id,
                ))
                session.execute(delete(SignatureGroupDB).where(SignatureGroupDB.id == group_id))
                session.execute(delete(SignatureDB).where(SignatureDB.id == signature_id))
                session.execute(delete(DomainDB).where(DomainDB.id == domain_id))

    def test_concurrent_group_messages_never_repeat_previous_signature(self):
        principal = f"authentik:{uuid.uuid4().hex}"
        domain_id = None
        group_id = uuid.uuid4().hex
        signature_ids = [uuid.uuid4().hex for _ in range(3)]
        with self.Session.begin() as session:
            domain = DomainDB(domain_name=f"{uuid.uuid4().hex}.example", is_active=True)
            group = SignatureGroupDB(id=group_id, name="Concurrency group", slug=f"cg-{group_id[:12]}", is_active=True)
            session.add_all([domain, group])
            session.flush()
            domain_id = domain.id
            session.add_all([
                SignatureDB(id=signature_id, name=f"Group {index}", scope="GLOBAL", mode="UNUSED", is_active=True)
                for index, signature_id in enumerate(signature_ids)
            ])
            session.add_all([
                SignatureGroupMemberDB(group_id=group_id, signature_id=signature_id, weight=1, is_enabled=True)
                for signature_id in signature_ids
            ])

        selected_in_transaction_order = []
        result_lock = threading.Lock()
        start = threading.Barrier(12)

        def select_one(_):
            with self.Session() as session:
                start.wait(timeout=15)
                selected = select_and_record_group_signature(session, principal, group_id, domain_id)
                self.assertIsNotNone(selected)
                with result_lock:
                    selected_in_transaction_order.append(selected.id)
                session.commit()
                return selected.id

        try:
            with ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(select_one, range(12)))
            self.assertEqual(len(results), 12)
            self.assertTrue(set(results).issubset(set(signature_ids)))
            self.assertTrue(
                all(previous != current for previous, current in zip(
                    selected_in_transaction_order, selected_in_transaction_order[1:]
                )),
                selected_in_transaction_order,
            )
        finally:
            with self.Session.begin() as session:
                session.execute(delete(GroupSelectionStateDB).where(
                    GroupSelectionStateDB.principal_id == principal,
                    GroupSelectionStateDB.group_id == group_id,
                ))
                session.execute(delete(SignatureGroupMemberDB).where(SignatureGroupMemberDB.group_id == group_id))
                session.execute(delete(SignatureGroupDB).where(SignatureGroupDB.id == group_id))
                session.execute(delete(SignatureDB).where(SignatureDB.id.in_(signature_ids)))
                session.execute(delete(DomainDB).where(DomainDB.id == domain_id))


if __name__ == "__main__":
    unittest.main()
