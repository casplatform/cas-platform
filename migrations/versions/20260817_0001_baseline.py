"""baseline: schema as it stood on 2026-08-17

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-17

Captured with pg_dump --schema-only from the production database, with
pg_dump's transport wrapper removed: the \\restrict meta-command (PostgreSQL
16, understood only by psql) and the session SET statements, neither of which
is schema and both of which SQLAlchemy would reject.

This records what already exists rather than building anything new. Production
and staging are marked with `alembic stamp 0001_baseline`, which writes the
version without running the SQL. Only a database created from scratch -- the
test database, or a future deployment -- actually executes it.

25 tables, 1 view, 20 sequences, 49 indexes, 46 constraints.
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_SQL = """
--
--

--
-- Name: admin_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_log (
    id integer NOT NULL,
    admin_id integer,
    action character varying(50) NOT NULL,
    target_user integer,
    details jsonb,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: admin_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: admin_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_log_id_seq OWNED BY public.admin_log.id;

--
-- Name: alert_sent; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_sent (
    id integer NOT NULL,
    email text NOT NULL,
    pair_key text NOT NULL,
    last_sent_at timestamp with time zone DEFAULT now() NOT NULL,
    last_pc double precision
);

--
-- Name: alert_sent_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alert_sent_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: alert_sent_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alert_sent_id_seq OWNED BY public.alert_sent.id;

--
-- Name: business_directory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.business_directory (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    country_code character varying(10),
    country_name character varying(100),
    category character varying(50) DEFAULT 'operator'::character varying,
    satellite_count integer DEFAULT 0,
    constellation character varying(100),
    website character varying(255),
    contact_email character varying(255),
    description text,
    hq_location character varying(255),
    founded character varying(20),
    stock_ticker character varying(30),
    data_source character varying(50) DEFAULT 'manual'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

--
-- Name: business_directory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.business_directory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: business_directory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.business_directory_id_seq OWNED BY public.business_directory.id;

--
-- Name: conjunction_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conjunction_events (
    id integer NOT NULL,
    fetched_at timestamp with time zone DEFAULT now(),
    cdm_id text,
    sat1 text,
    sat2 text,
    norad1 text,
    norad2 text,
    tca timestamp with time zone,
    miss_dist_m real,
    pc real,
    risk text,
    raw_json jsonb
);

--
-- Name: cas_observation_window; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.cas_observation_window AS
 SELECT min(fetched_at) AS first_observation,
    max(fetched_at) AS last_observation,
    GREATEST(1, (floor((EXTRACT(epoch FROM (max(fetched_at) - min(fetched_at))) / (86400)::numeric)))::integer) AS days_observing,
    count(DISTINCT cdm_id) AS unique_cdm_count
   FROM public.conjunction_events;

--
-- Name: conjunction_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conjunction_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: conjunction_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conjunction_events_id_seq OWNED BY public.conjunction_events.id;

--
-- Name: contact_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_submissions (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    email character varying(320) NOT NULL,
    organization character varying(200),
    subject character varying(50) NOT NULL,
    message text NOT NULL,
    ip_address character varying(64),
    user_agent text,
    status character varying(20) DEFAULT 'new'::character varying,
    submitted_at timestamp with time zone DEFAULT now(),
    replied_at timestamp with time zone
);

--
-- Name: contact_submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: contact_submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_submissions_id_seq OWNED BY public.contact_submissions.id;

--
-- Name: data_health; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_health (
    source text NOT NULL,
    last_success_at timestamp with time zone,
    last_attempt_at timestamp with time zone,
    status text DEFAULT 'unknown'::text,
    consecutive_failures integer DEFAULT 0,
    last_error text,
    mail_sent_at timestamp with time zone,
    expected_interval_minutes integer
);

--
-- Name: decision_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decision_results (
    id integer NOT NULL,
    user_id integer,
    watchlist_id integer,
    norad_id character varying(10) NOT NULL,
    sat_name character varying(255),
    recommendation character varying(30) DEFAULT 'monitor'::character varying NOT NULL,
    priority character varying(10) DEFAULT 'LOW'::character varying NOT NULL,
    confidence character varying(10) DEFAULT 'low'::character varying NOT NULL,
    max_pc double precision DEFAULT 0,
    min_miss_m double precision DEFAULT 0,
    total_conjunctions integer DEFAULT 0,
    red_count integer DEFAULT 0,
    yellow_count integer DEFAULT 0,
    green_count integer DEFAULT 0,
    tca_earliest timestamp with time zone,
    time_remaining_s double precision,
    time_remaining_str character varying(50),
    maneuver_summary text,
    delta_v_ms double precision,
    maneuver_direction character varying(20),
    alert_total integer DEFAULT 0,
    alert_review integer DEFAULT 0,
    alert_critical integer DEFAULT 0,
    cascade_result jsonb,
    detail_json jsonb,
    computed_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone,
    pc_trend_72h jsonb,
    risk_trend character varying(20) DEFAULT 'stable'::character varying,
    operator_action character varying(30),
    operator_action_at timestamp with time zone,
    operator_notes text
);

--
-- Name: decision_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.decision_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: decision_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.decision_results_id_seq OWNED BY public.decision_results.id;

--
-- Name: eusst_fg_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eusst_fg_events (
    id bigint NOT NULL,
    event_id text NOT NULL,
    eusst_internal_id bigint,
    creation_date timestamp with time zone,
    update_date timestamp with time zone,
    publish_date timestamp with time zone,
    event_epoch timestamp with time zone,
    originator text,
    product_id text,
    total_reports integer DEFAULT 0,
    parent1_object_name text,
    parent1_intl_designator text,
    parent1_norad_id text,
    parent1_object_type text,
    parent1_object_size text,
    parent1_apogee_km integer,
    parent1_perigee_km integer,
    parent2_object_name text,
    parent2_intl_designator text,
    parent2_norad_id text,
    parent2_object_type text,
    parent2_object_size text,
    parent2_apogee_km integer,
    parent2_perigee_km integer,
    frags_detected integer,
    autonomous text,
    orbit_regime text,
    fragmentation_type text,
    download_link text,
    file_name text,
    raw_payload jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: eusst_fg_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eusst_fg_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: eusst_fg_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eusst_fg_events_id_seq OWNED BY public.eusst_fg_events.id;

--
-- Name: eusst_re_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eusst_re_events (
    id bigint NOT NULL,
    event_id text NOT NULL,
    eusst_internal_id bigint,
    creation_date timestamp with time zone,
    update_date timestamp with time zone,
    publish_date timestamp with time zone,
    total_reports integer DEFAULT 0,
    object_name text,
    intl_designator text,
    norad_id text,
    object_type text,
    reentry_start_window timestamp with time zone,
    reentry_end_window timestamp with time zone,
    reentry_tca timestamp with time zone,
    inclination_deg numeric,
    apogee_km numeric,
    perigee_km numeric,
    risk_level text,
    aoi_list jsonb,
    download_link text,
    file_name text,
    raw_payload jsonb NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    object_size text,
    reentry_altitude text,
    decay text,
    autonomous text,
    risk_level_comment text,
    max_latitude numeric
);

--
-- Name: eusst_re_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eusst_re_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: eusst_re_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eusst_re_events_id_seq OWNED BY public.eusst_re_events.id;

--
-- Name: eusst_sync_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eusst_sync_state (
    service text NOT NULL,
    last_sync_at timestamp with time zone,
    last_update_date timestamp with time zone,
    events_total integer DEFAULT 0,
    last_status text,
    last_error text,
    notes text
);

--
-- Name: historical_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historical_events (
    id integer NOT NULL,
    source_cdm_id text,
    sat1 text NOT NULL,
    sat2 text NOT NULL,
    norad1 text,
    norad2 text,
    tca timestamp with time zone NOT NULL,
    miss_dist_m real,
    pc real,
    risk_level text,
    cas_decision text,
    actual_outcome text,
    lessons_learned text,
    display_order integer DEFAULT 0,
    is_featured boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: historical_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.historical_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: historical_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.historical_events_id_seq OWNED BY public.historical_events.id;

--
-- Name: insurance_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insurance_reports (
    id integer NOT NULL,
    report_id character varying(12) NOT NULL,
    user_id integer NOT NULL,
    org character varying(120),
    subject character varying(160) NOT NULL,
    altitude_km real NOT NULL,
    inclination_deg real,
    mode character varying(24),
    lambda_per_year double precision,
    threat_objects integer,
    trend_pct real,
    cascade_years real,
    assessment jsonb NOT NULL,
    catalogue_epoch timestamp with time zone,
    is_demo boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: insurance_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.insurance_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: insurance_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.insurance_reports_id_seq OWNED BY public.insurance_reports.id;

--
-- Name: insurance_watch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insurance_watch (
    id integer NOT NULL,
    user_id integer NOT NULL,
    label character varying(120) NOT NULL,
    altitude_km real NOT NULL,
    inclination_deg real,
    band_half_km real DEFAULT 25.0,
    watch_threat boolean DEFAULT true,
    threat_pct real DEFAULT 10.0,
    watch_pctl boolean DEFAULT true,
    pctl_points real DEFAULT 5.0,
    watch_frag boolean DEFAULT true,
    baseline_threat integer,
    baseline_pctl integer,
    baseline_at timestamp with time zone DEFAULT now(),
    last_checked timestamp with time zone,
    last_triggered timestamp with time zone,
    trigger_count integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: insurance_watch_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insurance_watch_events (
    id integer NOT NULL,
    watch_id integer NOT NULL,
    user_id integer NOT NULL,
    trigger_type character varying(24) NOT NULL,
    old_value real,
    new_value real,
    delta real,
    detail jsonb,
    notified boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: insurance_watch_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.insurance_watch_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: insurance_watch_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.insurance_watch_events_id_seq OWNED BY public.insurance_watch_events.id;

--
-- Name: insurance_watch_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.insurance_watch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: insurance_watch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.insurance_watch_id_seq OWNED BY public.insurance_watch.id;

--
-- Name: leo_debris_ranking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.leo_debris_ranking (
    id bigint NOT NULL,
    snapshot_week date NOT NULL,
    band text NOT NULL,
    rank integer NOT NULL,
    norad_id text NOT NULL,
    object_name text NOT NULL,
    cdm_count integer DEFAULT 0 NOT NULL,
    unique_counterparties integer DEFAULT 0 NOT NULL,
    max_pc double precision,
    cumulative_pc double precision,
    threat_score double precision,
    avg_altitude_km double precision,
    first_seen timestamp with time zone,
    last_seen timestamp with time zone,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: leo_debris_ranking_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.leo_debris_ranking_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: leo_debris_ranking_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.leo_debris_ranking_id_seq OWNED BY public.leo_debris_ranking.id;

--
-- Name: login_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_log (
    id integer NOT NULL,
    user_id integer,
    email character varying(255),
    login_at timestamp with time zone DEFAULT now(),
    ip_address character varying(64),
    user_agent text,
    success boolean DEFAULT false,
    failure_reason character varying(100)
);

--
-- Name: login_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.login_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: login_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.login_log_id_seq OWNED BY public.login_log.id;

--
-- Name: notification_prefs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_prefs (
    user_id integer NOT NULL,
    alert_email boolean DEFAULT true,
    min_risk character varying(10) DEFAULT 'RED'::character varying,
    slack_url text,
    teams_url text,
    webhook_url text,
    webhook_secret text
);

--
-- Name: satcat_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.satcat_objects (
    norad integer NOT NULL,
    name text,
    object_type text,
    rcs_size text,
    rcs_value real,
    country text,
    launch_date date,
    decay_date date,
    apogee_km integer,
    perigee_km integer,
    inclination real,
    mass_kg real,
    mass_source text,
    updated_at timestamp with time zone DEFAULT now(),
    xsect_m2 real,
    shape text,
    height_m real,
    width_m real,
    depth_m real,
    diameter_m real
);

--
-- Name: space_weather_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.space_weather_snapshots (
    id integer NOT NULL,
    fetched_at timestamp with time zone DEFAULT now(),
    kp_index real,
    kp_estimated real,
    kp_label text,
    kp_status text,
    f107_flux real,
    f107_status text,
    xray_class text,
    xray_flux_peak real,
    xray_status text,
    active_alerts jsonb,
    raw_summary jsonb
);

--
-- Name: space_weather_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.space_weather_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: space_weather_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.space_weather_snapshots_id_seq OWNED BY public.space_weather_snapshots.id;

--
-- Name: support_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.support_tickets (
    id integer NOT NULL,
    user_id integer,
    email text NOT NULL,
    role text,
    portal text,
    category text,
    subject text NOT NULL,
    body text NOT NULL,
    screenshot text,
    context jsonb,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: support_tickets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.support_tickets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: support_tickets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.support_tickets_id_seq OWNED BY public.support_tickets.id;

--
-- Name: tle_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tle_history (
    norad integer NOT NULL,
    epoch timestamp with time zone NOT NULL,
    l1 text NOT NULL,
    l2 text NOT NULL,
    inc_deg real,
    alt_km real,
    inserted_at timestamp with time zone DEFAULT now()
);

--
-- Name: user_activity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_activity (
    id integer NOT NULL,
    user_id integer,
    email character varying(255),
    action character varying(100),
    path character varying(500),
    details text,
    ip character varying(50),
    user_agent text,
    created_at timestamp with time zone DEFAULT now()
);

--
-- Name: user_activity_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_activity_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: user_activity_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_activity_id_seq OWNED BY public.user_activity.id;

--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    name character varying(255),
    role character varying(20) DEFAULT 'operator'::character varying,
    api_key character varying(64),
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    last_login timestamp with time zone,
    tier character varying(20) DEFAULT 'free'::character varying,
    max_satellites integer DEFAULT 1,
    tier_expires timestamp with time zone,
    email_verified boolean DEFAULT false,
    verification_token character varying(64),
    verification_token_expires timestamp with time zone,
    verification_sent_at timestamp with time zone,
    password_hash_type text DEFAULT 'sha256'::text
);

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;

--
-- Name: watchlist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist (
    id integer NOT NULL,
    user_id integer,
    norad_id character varying(10) NOT NULL,
    sat_name character varying(255) NOT NULL,
    tle_line1 text,
    tle_line2 text,
    altitude_km double precision,
    added_at timestamp with time zone DEFAULT now(),
    last_scan timestamp with time zone,
    regime character varying(10) DEFAULT 'leo'::character varying
);

--
-- Name: watchlist_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.watchlist_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: watchlist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.watchlist_id_seq OWNED BY public.watchlist.id;

--
-- Name: watchlist_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist_results (
    id integer NOT NULL,
    watchlist_id integer,
    user_id integer,
    scan_time timestamp with time zone DEFAULT now(),
    conjunctions jsonb DEFAULT '[]'::jsonb,
    red_count integer DEFAULT 0,
    yellow_count integer DEFAULT 0,
    green_count integer DEFAULT 0,
    cascade_result jsonb,
    scan_duration_s double precision
);

--
-- Name: watchlist_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.watchlist_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: watchlist_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.watchlist_results_id_seq OWNED BY public.watchlist_results.id;

--
-- Name: admin_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_log ALTER COLUMN id SET DEFAULT nextval('public.admin_log_id_seq'::regclass);

--
-- Name: alert_sent id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_sent ALTER COLUMN id SET DEFAULT nextval('public.alert_sent_id_seq'::regclass);

--
-- Name: business_directory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_directory ALTER COLUMN id SET DEFAULT nextval('public.business_directory_id_seq'::regclass);

--
-- Name: conjunction_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conjunction_events ALTER COLUMN id SET DEFAULT nextval('public.conjunction_events_id_seq'::regclass);

--
-- Name: contact_submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_submissions ALTER COLUMN id SET DEFAULT nextval('public.contact_submissions_id_seq'::regclass);

--
-- Name: decision_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_results ALTER COLUMN id SET DEFAULT nextval('public.decision_results_id_seq'::regclass);

--
-- Name: eusst_fg_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_fg_events ALTER COLUMN id SET DEFAULT nextval('public.eusst_fg_events_id_seq'::regclass);

--
-- Name: eusst_re_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_re_events ALTER COLUMN id SET DEFAULT nextval('public.eusst_re_events_id_seq'::regclass);

--
-- Name: historical_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_events ALTER COLUMN id SET DEFAULT nextval('public.historical_events_id_seq'::regclass);

--
-- Name: insurance_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_reports ALTER COLUMN id SET DEFAULT nextval('public.insurance_reports_id_seq'::regclass);

--
-- Name: insurance_watch id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_watch ALTER COLUMN id SET DEFAULT nextval('public.insurance_watch_id_seq'::regclass);

--
-- Name: insurance_watch_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_watch_events ALTER COLUMN id SET DEFAULT nextval('public.insurance_watch_events_id_seq'::regclass);

--
-- Name: leo_debris_ranking id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leo_debris_ranking ALTER COLUMN id SET DEFAULT nextval('public.leo_debris_ranking_id_seq'::regclass);

--
-- Name: login_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_log ALTER COLUMN id SET DEFAULT nextval('public.login_log_id_seq'::regclass);

--
-- Name: space_weather_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_weather_snapshots ALTER COLUMN id SET DEFAULT nextval('public.space_weather_snapshots_id_seq'::regclass);

--
-- Name: support_tickets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets ALTER COLUMN id SET DEFAULT nextval('public.support_tickets_id_seq'::regclass);

--
-- Name: user_activity id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_activity ALTER COLUMN id SET DEFAULT nextval('public.user_activity_id_seq'::regclass);

--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);

--
-- Name: watchlist id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist ALTER COLUMN id SET DEFAULT nextval('public.watchlist_id_seq'::regclass);

--
-- Name: watchlist_results id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_results ALTER COLUMN id SET DEFAULT nextval('public.watchlist_results_id_seq'::regclass);

--
-- Name: admin_log admin_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_log
    ADD CONSTRAINT admin_log_pkey PRIMARY KEY (id);

--
-- Name: alert_sent alert_sent_email_pair_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_sent
    ADD CONSTRAINT alert_sent_email_pair_key_key UNIQUE (email, pair_key);

--
-- Name: alert_sent alert_sent_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_sent
    ADD CONSTRAINT alert_sent_pkey PRIMARY KEY (id);

--
-- Name: business_directory business_directory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_directory
    ADD CONSTRAINT business_directory_pkey PRIMARY KEY (id);

--
-- Name: conjunction_events conjunction_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conjunction_events
    ADD CONSTRAINT conjunction_events_pkey PRIMARY KEY (id);

--
-- Name: contact_submissions contact_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_submissions
    ADD CONSTRAINT contact_submissions_pkey PRIMARY KEY (id);

--
-- Name: data_health data_health_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_health
    ADD CONSTRAINT data_health_pkey PRIMARY KEY (source);

--
-- Name: decision_results decision_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_results
    ADD CONSTRAINT decision_results_pkey PRIMARY KEY (id);

--
-- Name: decision_results decision_results_user_norad_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_results
    ADD CONSTRAINT decision_results_user_norad_uq UNIQUE (user_id, norad_id);

--
-- Name: eusst_fg_events eusst_fg_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_fg_events
    ADD CONSTRAINT eusst_fg_events_event_id_key UNIQUE (event_id);

--
-- Name: eusst_fg_events eusst_fg_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_fg_events
    ADD CONSTRAINT eusst_fg_events_pkey PRIMARY KEY (id);

--
-- Name: eusst_re_events eusst_re_events_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_re_events
    ADD CONSTRAINT eusst_re_events_event_id_key UNIQUE (event_id);

--
-- Name: eusst_re_events eusst_re_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_re_events
    ADD CONSTRAINT eusst_re_events_pkey PRIMARY KEY (id);

--
-- Name: eusst_sync_state eusst_sync_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eusst_sync_state
    ADD CONSTRAINT eusst_sync_state_pkey PRIMARY KEY (service);

--
-- Name: historical_events historical_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_events
    ADD CONSTRAINT historical_events_pkey PRIMARY KEY (id);

--
-- Name: historical_events historical_events_source_cdm_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_events
    ADD CONSTRAINT historical_events_source_cdm_id_key UNIQUE (source_cdm_id);

--
-- Name: insurance_reports insurance_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_reports
    ADD CONSTRAINT insurance_reports_pkey PRIMARY KEY (id);

--
-- Name: insurance_watch_events insurance_watch_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_watch_events
    ADD CONSTRAINT insurance_watch_events_pkey PRIMARY KEY (id);

--
-- Name: insurance_watch insurance_watch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_watch
    ADD CONSTRAINT insurance_watch_pkey PRIMARY KEY (id);

--
-- Name: leo_debris_ranking leo_debris_ranking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leo_debris_ranking
    ADD CONSTRAINT leo_debris_ranking_pkey PRIMARY KEY (id);

--
-- Name: login_log login_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_log
    ADD CONSTRAINT login_log_pkey PRIMARY KEY (id);

--
-- Name: notification_prefs notification_prefs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_prefs
    ADD CONSTRAINT notification_prefs_pkey PRIMARY KEY (user_id);

--
-- Name: satcat_objects satcat_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.satcat_objects
    ADD CONSTRAINT satcat_objects_pkey PRIMARY KEY (norad);

--
-- Name: space_weather_snapshots space_weather_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_weather_snapshots
    ADD CONSTRAINT space_weather_snapshots_pkey PRIMARY KEY (id);

--
-- Name: support_tickets support_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_pkey PRIMARY KEY (id);

--
-- Name: business_directory uq_bizdir_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.business_directory
    ADD CONSTRAINT uq_bizdir_name UNIQUE (name);

--
-- Name: conjunction_events uq_conjunction_cdm_fetched; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conjunction_events
    ADD CONSTRAINT uq_conjunction_cdm_fetched UNIQUE (cdm_id, fetched_at);

--
-- Name: insurance_reports uq_ins_report; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_reports
    ADD CONSTRAINT uq_ins_report UNIQUE (report_id, user_id);

--
-- Name: tle_history uq_tle_norad_epoch; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tle_history
    ADD CONSTRAINT uq_tle_norad_epoch UNIQUE (norad, epoch);

--
-- Name: user_activity user_activity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_activity
    ADD CONSTRAINT user_activity_pkey PRIMARY KEY (id);

--
-- Name: users users_api_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_api_key_key UNIQUE (api_key);

--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);

--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

--
-- Name: watchlist watchlist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_pkey PRIMARY KEY (id);

--
-- Name: watchlist_results watchlist_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_results
    ADD CONSTRAINT watchlist_results_pkey PRIMARY KEY (id);

--
-- Name: watchlist watchlist_user_id_norad_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_user_id_norad_id_key UNIQUE (user_id, norad_id);

--
-- Name: idx_activity_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_activity_action ON public.user_activity USING btree (action);

--
-- Name: idx_activity_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_activity_time ON public.user_activity USING btree (created_at DESC);

--
-- Name: idx_activity_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_activity_user ON public.user_activity USING btree (user_id);

--
-- Name: idx_admin_log_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_log_time ON public.admin_log USING btree (created_at DESC);

--
-- Name: idx_bizdir_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bizdir_category ON public.business_directory USING btree (category);

--
-- Name: idx_bizdir_country; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bizdir_country ON public.business_directory USING btree (country_code);

--
-- Name: idx_ce_cdm_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_cdm_id ON public.conjunction_events USING btree (cdm_id);

--
-- Name: idx_ce_fetched_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_fetched_at ON public.conjunction_events USING btree (fetched_at DESC);

--
-- Name: idx_ce_norad1_fetched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_norad1_fetched ON public.conjunction_events USING btree (norad1, fetched_at DESC);

--
-- Name: idx_ce_norad2_fetched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_norad2_fetched ON public.conjunction_events USING btree (norad2, fetched_at DESC);

--
-- Name: idx_ce_risk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_risk ON public.conjunction_events USING btree (risk);

--
-- Name: idx_contact_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contact_status ON public.contact_submissions USING btree (status);

--
-- Name: idx_contact_submitted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contact_submitted ON public.contact_submissions USING btree (submitted_at DESC);

--
-- Name: idx_decision_norad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_decision_norad ON public.decision_results USING btree (norad_id);

--
-- Name: idx_decision_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_decision_priority ON public.decision_results USING btree (priority);

--
-- Name: idx_decision_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_decision_time ON public.decision_results USING btree (computed_at DESC);

--
-- Name: idx_decision_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_decision_user ON public.decision_results USING btree (user_id);

--
-- Name: idx_eusst_fg_event_epoch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_fg_event_epoch ON public.eusst_fg_events USING btree (event_epoch DESC);

--
-- Name: idx_eusst_fg_orbit_regime; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_fg_orbit_regime ON public.eusst_fg_events USING btree (orbit_regime);

--
-- Name: idx_eusst_fg_parent1_norad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_fg_parent1_norad ON public.eusst_fg_events USING btree (parent1_norad_id);

--
-- Name: idx_eusst_fg_update_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_fg_update_date ON public.eusst_fg_events USING btree (update_date DESC);

--
-- Name: idx_eusst_re_norad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_re_norad ON public.eusst_re_events USING btree (norad_id);

--
-- Name: idx_eusst_re_object_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_re_object_type ON public.eusst_re_events USING btree (object_type);

--
-- Name: idx_eusst_re_reentry_tca; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_re_reentry_tca ON public.eusst_re_events USING btree (reentry_tca);

--
-- Name: idx_eusst_re_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eusst_re_severity ON public.eusst_re_events USING btree (risk_level);

--
-- Name: idx_historical_events_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historical_events_order ON public.historical_events USING btree (display_order, tca DESC);

--
-- Name: idx_ins_rep_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_rep_created ON public.insurance_reports USING btree (created_at DESC);

--
-- Name: idx_ins_rep_repid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_rep_repid ON public.insurance_reports USING btree (report_id);

--
-- Name: idx_ins_rep_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_rep_user ON public.insurance_reports USING btree (user_id, created_at DESC);

--
-- Name: idx_ins_watch_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_watch_active ON public.insurance_watch USING btree (is_active, last_checked);

--
-- Name: idx_ins_watch_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_watch_user ON public.insurance_watch USING btree (user_id, is_active);

--
-- Name: idx_ins_wev_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ins_wev_user ON public.insurance_watch_events USING btree (user_id, created_at DESC);

--
-- Name: idx_ldr_norad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ldr_norad ON public.leo_debris_ranking USING btree (norad_id);

--
-- Name: idx_ldr_snapshot_band; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ldr_snapshot_band ON public.leo_debris_ranking USING btree (snapshot_week DESC, band, rank);

--
-- Name: idx_login_log_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_login_log_email ON public.login_log USING btree (email, login_at DESC);

--
-- Name: idx_login_log_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_login_log_time ON public.login_log USING btree (login_at DESC);

--
-- Name: idx_login_log_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_login_log_user ON public.login_log USING btree (user_id, login_at DESC);

--
-- Name: idx_satcat_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_satcat_name ON public.satcat_objects USING btree (lower(name));

--
-- Name: idx_satcat_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_satcat_type ON public.satcat_objects USING btree (object_type);

--
-- Name: idx_support_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_support_status ON public.support_tickets USING btree (status, created_at DESC);

--
-- Name: idx_swx_fetched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_swx_fetched ON public.space_weather_snapshots USING btree (fetched_at DESC);

--
-- Name: idx_tleh_alt; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tleh_alt ON public.tle_history USING btree (alt_km);

--
-- Name: idx_tleh_alt_epoch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tleh_alt_epoch ON public.tle_history USING btree (alt_km, epoch);

--
-- Name: idx_tleh_alt_epoch_inc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tleh_alt_epoch_inc ON public.tle_history USING btree (alt_km, epoch, inc_deg);

--
-- Name: idx_tleh_norad_epoch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tleh_norad_epoch ON public.tle_history USING btree (norad, epoch);

--
-- Name: idx_users_verification_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_users_verification_token ON public.users USING btree (verification_token) WHERE (verification_token IS NOT NULL);

--
-- Name: idx_watchlist_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_user ON public.watchlist USING btree (user_id);

--
-- Name: idx_wresults_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wresults_time ON public.watchlist_results USING btree (scan_time DESC);

--
-- Name: idx_wresults_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wresults_user ON public.watchlist_results USING btree (user_id);

--
-- Name: admin_log admin_log_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_log
    ADD CONSTRAINT admin_log_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.users(id);

--
-- Name: decision_results decision_results_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_results
    ADD CONSTRAINT decision_results_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

--
-- Name: decision_results decision_results_watchlist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_results
    ADD CONSTRAINT decision_results_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES public.watchlist(id) ON DELETE CASCADE;

--
-- Name: insurance_watch_events insurance_watch_events_watch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insurance_watch_events
    ADD CONSTRAINT insurance_watch_events_watch_id_fkey FOREIGN KEY (watch_id) REFERENCES public.insurance_watch(id) ON DELETE CASCADE;

--
-- Name: login_log login_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_log
    ADD CONSTRAINT login_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;

--
-- Name: notification_prefs notification_prefs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_prefs
    ADD CONSTRAINT notification_prefs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

--
-- Name: support_tickets support_tickets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;

--
-- Name: watchlist_results watchlist_results_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_results
    ADD CONSTRAINT watchlist_results_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

--
-- Name: watchlist_results watchlist_results_watchlist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_results
    ADD CONSTRAINT watchlist_results_watchlist_id_fkey FOREIGN KEY (watchlist_id) REFERENCES public.watchlist(id) ON DELETE CASCADE;

--
-- Name: watchlist watchlist_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

--
--
"""


def upgrade() -> None:
    op.execute(BASELINE_SQL)


def downgrade() -> None:
    # A baseline has no earlier state to return to; the only honest downgrade
    # would drop every table, which is a data-loss command disguised as a
    # routine one. Restore from a dump instead.
    raise NotImplementedError(
        "0001_baseline cannot be downgraded -- restore from a database backup."
    )
