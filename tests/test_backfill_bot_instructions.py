"""Tests for scripts/backfill_bot_instructions.py."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.chat.presets import PRESET_SUPPORT_AGENT, effective_agent_instructions
from backend.models import Bot, Tenant
from scripts.backfill_bot_instructions import (
    EXCLUDED,
    PRESET_ONLY,
    SKIP,
    SPLIT,
    UNRECOGNIZED,
    BackfillAbort,
    run_backfill,
)
from scripts.refresh_bot_instructions import _PRESET_GEN_1, _PRESET_GEN_2


def _make_bot(
    session_local: sessionmaker,
    *,
    agent_instructions: str | None,
    bot_id: uuid.UUID | None = None,
) -> uuid.UUID:
    with session_local() as db:
        tenant = Tenant(name="Backfill Tenant")
        db.add(tenant)
        db.flush()
        bot = Bot(
            id=bot_id or uuid.uuid4(),
            tenant_id=tenant.id,
            name="Backfill Bot",
            agent_instructions=agent_instructions,
        )
        db.add(bot)
        db.commit()
        return bot.id


def _reload(session_local: sessionmaker, bot_id: uuid.UUID) -> Bot:
    with session_local() as db:
        return db.get(Bot, bot_id)


def test_preset_only_gen1(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions=_PRESET_GEN_1)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[PRESET_ONLY] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions is None
    assert bot.preset == "support_agent"


def test_preset_only_gen2(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions=_PRESET_GEN_2)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[PRESET_ONLY] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions is None
    assert bot.preset == "support_agent"


def test_preset_only_current(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions=PRESET_SUPPORT_AGENT)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[PRESET_ONLY] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions is None
    assert bot.preset == "support_agent"


def test_split_description_plus_gen2(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    description = "Acme ships industrial widgets to 40 countries."
    stored = f"{description}\n\n{_PRESET_GEN_2.strip()}"
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[SPLIT] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == description
    assert bot.preset == "support_agent"


def test_split_description_plus_gen1_plus_tail(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    description = "Acme ships industrial widgets to 40 countries."
    owner_rules = "Refund window is 14 days. Never mention competitors."
    stored = f"{description}\n\n{_PRESET_GEN_1.strip()}\n\n{owner_rules}"
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[SPLIT] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == f"{description}\n\n{owner_rules}"
    assert bot.preset == "support_agent"


def test_unrecognized(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    stored = "Always answer in haiku."
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, lines = run_backfill(apply=True, session_factory=session_local)

    assert stats[UNRECOGNIZED] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == stored
    assert bot.preset is None
    assert any(f"{bot_id}" in line and UNRECOGNIZED in line for line in lines)

    text, source = effective_agent_instructions(
        agent_instructions=bot.agent_instructions,
        custom_instructions=bot.custom_instructions,
        preset=bot.preset,
    )
    assert text == stored
    assert source == "custom"


def test_repeated_known_block_is_unrecognized(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    description = "Acme ships industrial widgets to 40 countries."
    tail = "Refund window is 14 days."
    stored = f"{description}\n\n{_PRESET_GEN_2.strip()}\n\n{_PRESET_GEN_2.strip()}\n\n{tail}"
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[UNRECOGNIZED] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == stored
    assert bot.preset is None


def test_hand_edited_preset_is_unrecognized(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    stored = _PRESET_GEN_2.strip().replace("Keep it concise.", "Keep it short.")
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[UNRECOGNIZED] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == stored
    assert bot.preset is None


def test_unknown_exclude_id_aborts_before_writes(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions="Some legacy text.")

    try:
        run_backfill(
            apply=True,
            exclude_ids=["ffffffff"],
            session_factory=session_local,
        )
        raised = False
    except BackfillAbort:
        raised = True
    assert raised

    # Nothing was written: the unmatched exclusion is detected before any row is touched.
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions == "Some legacy text."


def test_excluded_by_explicit_full_id(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    stored = "Hand-authored persona text."
    bot_id = _make_bot(session_local, agent_instructions=stored)

    stats, _ = run_backfill(
        apply=True, exclude_ids=[str(bot_id)], session_factory=session_local
    )

    assert stats[EXCLUDED] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == stored
    assert bot.preset is None

    text, source = effective_agent_instructions(
        agent_instructions=bot.agent_instructions,
        custom_instructions=bot.custom_instructions,
        preset=bot.preset,
    )
    assert text == stored
    assert source == "custom"


def test_excluded_by_default_prefix(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    persona_id = uuid.UUID("8e9956e9-1111-1111-1111-111111111111")
    stored = "Persona bot's own authored text."
    _make_bot(session_local, agent_instructions=stored, bot_id=persona_id)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[EXCLUDED] == 1
    bot = _reload(session_local, persona_id)
    assert bot.agent_instructions is None
    assert bot.custom_instructions == stored
    assert bot.preset is None


def test_empty_is_skip(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions=None)

    stats, _ = run_backfill(apply=True, session_factory=session_local)

    assert stats[SKIP] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions is None


def test_dry_run_writes_nothing(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _make_bot(session_local, agent_instructions=_PRESET_GEN_2)

    stats, _ = run_backfill(apply=False, session_factory=session_local)

    assert stats[PRESET_ONLY] == 1
    bot = _reload(session_local, bot_id)
    assert bot.agent_instructions == _PRESET_GEN_2
    assert bot.custom_instructions is None


def test_second_apply_run_is_noop(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    description = "Acme ships industrial widgets to 40 countries."
    stored = f"{description}\n\n{_PRESET_GEN_2.strip()}"
    _make_bot(session_local, agent_instructions=stored)

    first, _ = run_backfill(apply=True, session_factory=session_local)
    second, _ = run_backfill(apply=True, session_factory=session_local)

    assert first[SPLIT] == 1
    assert second == {SKIP: 1}


def test_ambiguous_prefix_aborts(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    id_a = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    id_b = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
    _make_bot(session_local, agent_instructions="Text A.", bot_id=id_a)
    _make_bot(session_local, agent_instructions="Text B.", bot_id=id_b)

    try:
        run_backfill(apply=True, exclude_ids=["aaaaaaaa"], session_factory=session_local)
        raised = False
    except BackfillAbort:
        raised = True
    assert raised

    # Nothing was written: the ambiguity is detected before any row is touched.
    assert _reload(session_local, id_a).agent_instructions == "Text A."
    assert _reload(session_local, id_b).agent_instructions == "Text B."


def test_effective_instructions_after_split_migration(engine: Engine) -> None:
    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    description = "Acme ships industrial widgets to 40 countries."
    owner_rules = "Refund window is 14 days."
    stored = f"{description}\n\n{_PRESET_GEN_2.strip()}\n\n{owner_rules}"
    bot_id = _make_bot(session_local, agent_instructions=stored)

    run_backfill(apply=True, session_factory=session_local)

    bot = _reload(session_local, bot_id)
    text, source = effective_agent_instructions(
        agent_instructions=bot.agent_instructions,
        custom_instructions=bot.custom_instructions,
        preset=bot.preset,
    )
    assert source == "preset+custom"
    assert description in text
    assert owner_rules in text
    assert PRESET_SUPPORT_AGENT.strip() in text
