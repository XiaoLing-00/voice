# service/schema.py


class SchemaInitializer:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        # ── 岗位表 ───────────────────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS job_position (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                tech_stack  TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL
            )
        """)

        # ── 题库（含岗位关联） ────────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS question_bank (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_position_id INTEGER NOT NULL DEFAULT 0,
                q_type          TEXT NOT NULL,
                difficulty      TEXT NOT NULL,
                content         TEXT NOT NULL,
                answer          TEXT NOT NULL,
                tags            TEXT DEFAULT '[]',
                FOREIGN KEY (job_position_id) REFERENCES job_position(id)
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_qb_position_type
            ON question_bank (job_position_id, q_type, difficulty)
        """)

        # ── 学生表 ───────────────────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS student (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                email      TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # ── 面试会话 ─────────────────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS interview_session (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id      INTEGER NOT NULL,
                job_position_id INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'ongoing',
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                overall_score   REAL,
                report          TEXT,
                FOREIGN KEY (student_id)      REFERENCES student(id),
                FOREIGN KEY (job_position_id) REFERENCES job_position(id)
            )
        """)

        # ── 面试轮次（每一问一答） ────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS interview_turn (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     INTEGER NOT NULL,
                turn_index     INTEGER NOT NULL,
                question_text  TEXT NOT NULL,
                student_answer TEXT NOT NULL DEFAULT '',
                ai_followup    TEXT,
                scores         TEXT,
                audio_path     TEXT,
                created_at     TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES interview_session(id)
            )
        """)

        # ── RAG 知识库分块 ────────────────────────────────────────────────────
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunk (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_position_id INTEGER NOT NULL DEFAULT 0,
                source          TEXT NOT NULL,
                chunk_text      TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_kc_position
            ON knowledge_chunk (job_position_id)
        """)

        self._seed_positions()

    # ── 内置两个演示岗位 ──────────────────────────────────────────────────────
    def _seed_positions(self):
        existing = self.db.fetchone("SELECT COUNT(*) FROM job_position")[0]
        if existing > 0:
            return

        import json
        from datetime import datetime
        now = datetime.now().isoformat()

        positions = [
            (
                "Java 后端工程师",
                "面向服务端开发方向，考察 Java 基础、Spring 生态、数据库、分布式系统等",
                json.dumps(["Java", "Spring Boot", "MySQL", "Redis", "MyBatis",
                            "消息队列", "分布式", "JVM", "多线程"], ensure_ascii=False),
                now,
            ),
            (
                "前端开发工程师",
                "面向 Web 前端方向，考察 JavaScript、框架、工程化、性能优化等",
                json.dumps(["JavaScript", "TypeScript", "Vue", "React",
                            "HTML/CSS", "Webpack", "浏览器原理", "网络"], ensure_ascii=False),
                now,
            ),
        ]
        self.db.executemany(
            "INSERT INTO job_position (name, description, tech_stack, created_at) VALUES (?,?,?,?)",
            positions,
        )
