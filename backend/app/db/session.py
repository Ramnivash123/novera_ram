"""
Database session management with async support.
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import event, text
from loguru import logger

from app.core.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def run_schema_fixes(conn) -> None:
    """
    Add missing columns to existing tables. Safe to run every startup.
    Uses nested BEGIN/EXCEPTION blocks to skip existing columns.
    """
    logger.info("🔄 Checking and fixing database schema...")

    # Each statement is independent — if one fails, others still run
    statements = [
        # ========== ORGANIZATIONS ==========
        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN slug VARCHAR(255);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN display_name VARCHAR(255);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN description VARCHAR(1000);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN max_users INTEGER;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN max_documents INTEGER;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN max_storage_gb INTEGER;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN settings JSONB DEFAULT '{}';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN created_by UUID;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE organizations ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        # Populate slug and display_name
        """UPDATE organizations 
           SET slug = LOWER(REPLACE(REPLACE(REPLACE(name, ' ', '-'), '_', '-'), '.', '-'))
           WHERE slug IS NULL""",

        """UPDATE organizations 
           SET display_name = INITCAP(REPLACE(REPLACE(name, '-', ' '), '_', ' '))
           WHERE display_name IS NULL""",

        # Slug unique index
        """DO $$ BEGIN
            CREATE UNIQUE INDEX ix_organizations_slug ON organizations(slug);
        EXCEPTION WHEN duplicate_table THEN NULL;
        END $$""",

        # ========== USERS ==========
        """DO $$ BEGIN
            ALTER TABLE users ADD COLUMN organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE users ADD COLUMN preferences JSONB DEFAULT '{}';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE users ADD COLUMN metadata JSONB DEFAULT '{}';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            CREATE INDEX ix_users_organization_id ON users(organization_id);
        EXCEPTION WHEN duplicate_table THEN NULL;
        END $$""",

        # ========== REFRESH TOKENS ==========
        """DO $$ BEGIN
            ALTER TABLE refresh_tokens ADD COLUMN user_agent VARCHAR(512);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        """DO $$ BEGIN
            ALTER TABLE refresh_tokens ADD COLUMN ip_address VARCHAR(45);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        # ========== PASSWORD RESET TOKENS ==========
        """DO $$ BEGIN
            ALTER TABLE password_reset_tokens ADD COLUMN ip_address VARCHAR(45);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        # ========== EMAIL VERIFICATION TOKENS ==========
        """DO $$ BEGIN
            ALTER TABLE email_verification_tokens ADD COLUMN ip_address VARCHAR(45);
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$""",

        # ========== ORGANIZATION CUSTOMIZATIONS ==========
        # All in one block with nested BEGIN/EXCEPTION
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'organization_customizations') THEN
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN dark_mode_enabled BOOLEAN DEFAULT FALSE;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN dark_mode_colors JSONB;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN app_name VARCHAR(255);
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN app_tagline VARCHAR(512);
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN theme_name VARCHAR(100);
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
                BEGIN
                    ALTER TABLE organization_customizations ADD COLUMN theme_description VARCHAR(500);
                EXCEPTION WHEN duplicate_column THEN NULL;
                END;
            END IF;
        END $$""",

        # ========== SEED DEFAULT ORGANIZATION ==========
        """INSERT INTO organizations (id, name, slug, display_name, is_active, settings, created_at, updated_at)
           VALUES (gen_random_uuid(), 'default', 'default', 'Default Organization', TRUE, '{}', NOW(), NOW())
           ON CONFLICT (name) DO UPDATE SET
               slug = COALESCE(organizations.slug, EXCLUDED.slug),
               display_name = COALESCE(organizations.display_name, EXCLUDED.display_name),
               updated_at = NOW()""",

        # ========== ASSIGN ORPHAN USERS ==========
        """UPDATE users 
           SET organization_id = (SELECT id FROM organizations WHERE name = 'default' LIMIT 1)
           WHERE organization_id IS NULL""",
    ]

    applied = 0
    skipped = 0

    for i, stmt in enumerate(statements):
        try:
            await conn.execute(text(stmt))
            applied += 1
        except Exception as e:
            err = str(e).lower()
            if 'duplicate' in err or 'already exists' in err or 'does not exist' in err:
                skipped += 1
            else:
                logger.warning(f"⚠️ Statement {i+1} note: {str(e)[:150]}")
                skipped += 1

    logger.info(f"✅ Schema fixes: {applied} applied, {skipped} skipped")


async def init_db() -> None:
    """Initialize database on startup."""
    async with engine.begin() as conn:
        # Extensions
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("✅ pgvector extension enabled")

        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        logger.info("✅ pg_trgm extension enabled")

        # Create new tables (doesn't modify existing)
        await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified")

        # Fix existing tables — add missing columns
        await run_schema_fixes(conn)

        # Verify
        result = await conn.execute(
            text("SELECT id, name, slug, display_name FROM organizations WHERE name = 'default'")
        )
        org = result.fetchone()
        if org:
            logger.info(f"✅ Default org: name={org.name}, slug={org.slug}, display={org.display_name}")
        else:
            logger.error("❌ Default organization NOT found!")

        # Debug: show columns
        for tbl in ['organizations', 'users']:
            r = await conn.execute(text(
                f"SELECT column_name FROM information_schema.columns WHERE table_name='{tbl}' ORDER BY ordinal_position"
            ))
            cols = [row[0] for row in r.fetchall()]
            logger.info(f"📋 {tbl}: {', '.join(cols)}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
    logger.info("✅ Database connections closed")


@event.listens_for(engine.sync_engine, "connect")
def set_search_path(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("SET search_path TO public")
    cursor.execute("SET timezone TO 'UTC'")
    cursor.close()


__all__ = ["engine", "AsyncSessionLocal", "Base", "get_db", "init_db", "close_db"]
