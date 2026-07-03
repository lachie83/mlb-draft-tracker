suppressPackageStartupMessages({
  library(baseballr)
  library(DBI)
  library(RSQLite)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
db_path <- ifelse(length(args) >= 1, args[[1]], "../data/mlb_draft_2026.db")
year <- ifelse(length(args) >= 2, as.integer(args[[2]]), 2026)

con <- dbConnect(SQLite(), db_path)
on.exit(dbDisconnect(con), add = TRUE)

x <- mlb_draft_prospects(year = year)

normalize_bool <- function(v) {
  ifelse(is.na(v), 0L, ifelse(v, 1L, 0L))
}

out <- data.frame(
  mlb_person_id = x$person_id,
  bis_player_id = x$bis_player_id,
  full_name = x$person_full_name,
  first_name = x$person_first_name,
  last_name = x$person_last_name,
  use_name = x$person_use_name,
  use_last_name = x$person_use_last_name,
  rank = x$rank,
  position_code = x$person_primary_position_code,
  position_name = x$person_primary_position_name,
  position_type = x$person_primary_position_type,
  position_abbreviation = x$person_primary_position_abbreviation,
  bats = x$person_bat_side_code,
  throws = x$person_pitch_hand_code,
  school_name = x$school_name,
  school_class = x$school_school_class,
  school_state = x$school_state,
  school_country = x$school_country,
  home_city = x$home_city,
  home_state = x$home_state,
  home_country = x$home_country,
  birth_date = x$person_birth_date,
  current_age = x$person_current_age,
  birth_city = x$person_birth_city,
  birth_state_province = x$person_birth_state_province,
  birth_country = x$person_birth_country,
  height = x$person_height,
  weight = x$person_weight,
  active = normalize_bool(x$person_active),
  headshot_link = x$headshot_link,
  scouting_report = x$scouting_report,
  blurb = x$blurb,
  draft_year = year,
  draft_type_code = x$draft_type_code,
  draft_type_description = x$draft_type_description,
  is_drafted = normalize_bool(x$is_drafted),
  is_pass = normalize_bool(x$is_pass),
  pick_round = x$pick_round,
  pick_number = x$pick_number,
  draft_team_id = x$team_id,
  draft_team_name = x$team_name,
  draft_team_abbreviation = x$team_abbreviation,
  source = 'baseballr_mlb_draft_prospects',
  source_rank_updated_at = as.character(Sys.time()),
  source_pick_updated_at = as.character(Sys.time()),
  raw_payload = apply(x, 1, function(r) toJSON(as.list(r), auto_unbox = TRUE, null = 'null', na = 'null')),
  stringsAsFactors = FALSE
)

sql <- "
INSERT INTO prospects (
    mlb_person_id, bis_player_id, full_name, first_name, last_name, use_name, use_last_name,
    rank, position_code, position_name, position_type, position_abbreviation, bats, throws,
    school_name, school_class, school_state, school_country,
    home_city, home_state, home_country, birth_date, current_age,
    birth_city, birth_state_province, birth_country, height, weight, active,
    headshot_link, scouting_report, blurb, draft_year, draft_type_code, draft_type_description,
    is_drafted, is_pass, pick_round, pick_number, draft_team_id, draft_team_name,
    draft_team_abbreviation, source, source_rank_updated_at, source_pick_updated_at, raw_payload, updated_at
) VALUES (
    :mlb_person_id, :bis_player_id, :full_name, :first_name, :last_name, :use_name, :use_last_name,
    :rank, :position_code, :position_name, :position_type, :position_abbreviation, :bats, :throws,
    :school_name, :school_class, :school_state, :school_country,
    :home_city, :home_state, :home_country, :birth_date, :current_age,
    :birth_city, :birth_state_province, :birth_country, :height, :weight, :active,
    :headshot_link, :scouting_report, :blurb, :draft_year, :draft_type_code, :draft_type_description,
    :is_drafted, :is_pass, :pick_round, :pick_number, :draft_team_id, :draft_team_name,
    :draft_team_abbreviation, :source, :source_rank_updated_at, :source_pick_updated_at, :raw_payload, CURRENT_TIMESTAMP
)
ON CONFLICT(mlb_person_id, draft_year) DO UPDATE SET
    rank=excluded.rank,
    position_name=excluded.position_name,
    school_name=excluded.school_name,
    blurb=excluded.blurb,
    is_drafted=excluded.is_drafted,
    pick_round=excluded.pick_round,
    pick_number=excluded.pick_number,
    draft_team_id=excluded.draft_team_id,
    draft_team_name=excluded.draft_team_name,
    draft_team_abbreviation=excluded.draft_team_abbreviation,
    source_rank_updated_at=excluded.source_rank_updated_at,
    source_pick_updated_at=excluded.source_pick_updated_at,
    raw_payload=excluded.raw_payload,
    updated_at=CURRENT_TIMESTAMP
"

dbWithTransaction(con, {
  dbExecute(con, sql, params = unname(split(out, seq(nrow(out)))))
})

cat(sprintf("Synced %s prospects for %s\n", nrow(out), year))
