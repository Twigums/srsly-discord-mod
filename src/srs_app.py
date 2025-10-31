import pandas as pd
import sqlite3
import random

from datetime import datetime, timedelta, timezone
from functools import wraps

from pandas.core.frame import DataFrame
from src.dataclasses import SrsConfig

# decorator to handle if db connection is not established
# returns None if no connection
def check_conn(f):

    @wraps(f)
    def wrapper(*args, **kwargs):
        self = args[0]

        if self.conn is None:
            print(f"{f.__name__} failed. DB conn is {self.conn}.")

            return None

        return f(*args, **kwargs)

    return wrapper

class SrsApp:
    def __init__(self, config: SrsConfig):

        # relevant column name as a dictionary
        self.col_dict = {
            "current_grade_col": "CurrentGrade",
            "failure_col": "FailureCount",
            "success_col": "SuccessCount",
            "date_col": "NextAnswerDateISO",
            "vocab_col": "AssociatedVocab",
            "kanji_col": "AssociatedKanji",
            "id_col": "ID",
            "houhou_last_update_col": "LastUpdateDate",
            "houhou_creation_col": "CreationDate",
            "houhou_next_answer_col": "NextAnswerDate",
            "houhou_suspension_col": "SuspensionDate",
        }

        # set initial definitions from dataclass
        self.max_reviews_at_once = config.max_reviews_at_once
        self.entries_before_commit = config.entries_before_commit
        self.match_score_threshold = config.match_score_threshold
        self.srs_interval = config.srs_interval
        self.path_to_srs_db = config.path_to_srs_db
        self.path_to_full_db = config.path_to_full_db

        # variables shared between app and ui
        self.id_srs_db = "srs_db"
        self.name_srs_table = self.id_srs_db + ".SrsEntrySet"
        self.conn = None
        self.cursor = None
        self.entries_without_commit = 0
        self.due_review_ids = []
        self.len_review_ids = 0
        self.reset_review_variables()

    # reset a few variables
    def reset_review_variables(self) -> None:
        self.current_index = 0
        self.current_completed = 0
        self.stop_updating_review = False
        self.current_reviews = []

        return None

    # initialize sql connection to db
    def init_db(self) -> bool:

        # i have thought about having two connections, but there are a few cross database queries that need to be run
        # i guess another solution can be retrieving 2 tables using pd and operating on them using pd
        try:
            self.conn = sqlite3.connect(self.path_to_full_db)
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA busy_timeout = 30000")

        except sqlite3.Error as e:
            raise Exception(f"Conn failed: {e}")

            return False

        except FileNotFoundError as e:
            raise Exception("File not found: {e}")

            return False

        self.cursor = self.conn.cursor()
        self.cursor.execute(f"ATTACH DATABASE '{self.path_to_srs_db}' AS {self.id_srs_db};")

        return True

    # buffer for committing
    # prevents many commits at the same time
    @check_conn
    def to_commit(self) -> None:
        self.entries_without_commit += 1

        if self.entries_without_commit >= self.entries_before_commit:
            self.conn.commit()
            self.entries_without_commit = 0

        return None

    # reset # of entries without commit, and then commit
    @check_conn
    def force_commit(self) -> None:
        self.entries_without_commit = 0
        self.conn.commit()

        return None

    # close db by commiting all changes then closing the connection
    @check_conn
    def close_db(self) -> None:
        self.force_commit()
        self.conn.close()

        self.conn = None
        self.cursor = None

        return None

    # retrieve counts and ratio from db
    @check_conn
    def get_review_stats(self) -> tuple[DataFrame, DataFrame, DataFrame]:
        max_srs_grade = max(int(x) for x in self.srs_interval.keys())

        expected_values = "\n".join([f"SELECT {i} UNION ALL" for i in range(max_srs_grade)])

        q_current_grade_count = f"""
                                WITH expected(val) AS (
                                    {expected_values}
                                    SELECT {max_srs_grade}
                                )
                                SELECT expected.val AS val,
                                    COUNT(srs.{self.col_dict["current_grade_col"]})
                                FROM expected
                                LEFT JOIN {self.name_srs_table} AS srs ON srs.{self.col_dict["current_grade_col"]} = expected.val
                                GROUP BY expected.val
                                ORDER BY expected.val;
                                """

        # get the end of day today, but in utc! (items are stored using now -> utc time)
        q_today_review_count = f"""
                               SELECT COUNT(*) FROM {self.name_srs_table}
                               WHERE {self.col_dict["date_col"]} < datetime('now', 'localtime', 'start of day', '+1 day', '-1 second', 'utc');
                               """

        q_sucess_ratio = f"""
                         SELECT 
                             CASE
                             WHEN (SUM({self.col_dict["failure_col"]}) + SUM({self.col_dict["success_col"]})) = 0 THEN 0
                             ELSE SUM({self.col_dict["success_col"]}) * 1.0 / (SUM({self.col_dict["failure_col"]}) + SUM({self.col_dict["success_col"]}))
                             END AS ratio
                         FROM srs_db.SrsEntrySet
                         """

        df_grade_counts = pd.read_sql_query(q_current_grade_count, self.conn)
        df_today_counts = pd.read_sql_query(q_today_review_count, self.conn)
        df_ratio = pd.read_sql_query(q_sucess_ratio, self.conn)

        return df_grade_counts, df_today_counts, df_ratio

    # returns info on current item
    @check_conn
    def get_current_item(self) -> dict:
        if len(self.current_reviews) == 0:
            return None

        if self.current_index >= len(self.current_reviews):
            self.current_index = 0

        return self.current_reviews[self.current_index]

    # returns df on review items that have their next review date timestamp less than the current time
    # that means that item is ready for review
    @check_conn
    def get_due_reviews(self) -> DataFrame:

        # this will get all items that have their reviews BEFORE the current time in **UTC**
        q = f"""
            SELECT * FROM {self.name_srs_table}
            WHERE {self.col_dict["date_col"]} < current_timestamp;
            """

        df = pd.read_sql_query(q, self.conn)
        return df

    # returns df of all vocabs present in the user's srs review
    @check_conn
    def get_study_vocab(self) -> set:
        q = f"""
            SELECT {self.col_dict["vocab_col"]} FROM {self.name_srs_table};
            """

        df = pd.read_sql_query(q, self.conn)

        all_vocabs = set(df[self.col_dict["vocab_col"]].dropna())

        return all_vocabs

    # returns df of all kanji present in the user's srs review
    # this is a set of both their vocab and kanji
    # i should also blacklist all the hiragana and katakana, but it is what it is
    @check_conn
    def get_study_kanji(self) -> set:
        q = f"""
            SELECT {self.col_dict["vocab_col"]}, {self.col_dict["kanji_col"]} FROM {self.name_srs_table}
            WHERE LENGTH({self.col_dict["vocab_col"]}) = 1
            OR LENGTH({self.col_dict["kanji_col"]}) = 1;
            """

        df = pd.read_sql_query(q, self.conn)

        vocab_kanjis = set(df[self.col_dict["vocab_col"]].dropna())
        kanji_kanjis = set(df[kanji_col].dropna())

        all_kanjis = vocab_kanjis.union(kanji_kanjis)

        return all_kanjis

    @check_conn
    def filter_study_items(self, item_type: str, condition: str = "1=1") -> DataFrame:
        match item_type:
            case "vocab":
                item_col = self.col_dict["vocab_col"]

            case "kanji":
                item_col = self.col_dict["kanji_col"]

            case _:
                raise Exception

        q = f"""
            SELECT * FROM {self.name_srs_table}
            WHERE {item_col} IS NOT NULL
            AND {condition};
            """

        df = pd.read_sql_query(q, self.conn)
        return df

    # returns df of vocab that isn't present in our reviews given conditions
    # sort after using pd.sort_values to put nans at the end
    @check_conn
    def discover_new_vocab(self, condition: str = "v.JlptLevel IN (1, 2, 3, 4, 5)") -> DataFrame:
        q = f"""
            WITH v_except AS (
                SELECT * FROM VocabSet AS v
                WHERE {condition}
                AND NOT EXISTS (
                    SELECT 1 FROM {self.name_srs_table} AS srs
                    WHERE srs.{self.col_dict["vocab_col"]} = v.KanjiWriting
                    )
                )
            SELECT * FROM v_except
            JOIN VocabEntityVocabMeaning AS v_link ON v_link.VocabEntity_ID = v_except.ID
            JOIN VocabMeaningSet AS v_meaning ON v_link.Meanings_ID = v_meaning.ID
            JOIN VocabMeaningVocabCategory as v_cat_link ON v_cat_link.VocabMeaningVocabCategory_VocabCategory_ID = v_meaning.ID
            JOIN VocabCategorySet as v_cat ON v_cat.ID = v_cat_link.Categories_ID;
            """

        df = pd.read_sql_query(q, self.conn)
        return df

    # returns df of kanji that isn't present in our reviews given conditions
    # sort after using pd.sort_values to put nans at the end
    @check_conn
    def discover_new_kanji(self, condition: str = "k.JpltLevel IN (1, 2, 3, 4, 5)") -> DataFrame:
        q = f"""
            WITH k_except AS (
                SELECT * FROM KanjiSet AS k
                WHERE {condition}
                AND NOT EXISTS (
                    SELECT 1 FROM {self.name_srs_table} AS srs
                    WHERE srs.{self.col_dict["kanji_col"]} = k.Character
                    )
                )
            SELECT * FROM k_except as k
            JOIN KanjiMeaningSet AS k_meanings ON k_meanings.Kanji_ID = k.ID;
            """

        df = pd.read_sql_query(q, self.conn)
        return df

    # initialize the review session
    @check_conn
    def start_review_session(self) -> list:
        self.reset_review_variables()

        # get due reviews
        df = self.get_due_reviews()

        if df.empty:
            return []

        # sort them by when they were due, so the user can complete the earliest ones first
        sorted_df = df.sort_values(self.col_dict["date_col"], ascending = False)
        self.due_review_ids = sorted_df["ID"].tolist()
        self.len_review_ids = len(self.due_review_ids)

        current_ids = set()

        # makes sure that we add as many items to the review list without exceeding the max reviews defined
        while len(current_ids) < len(sorted_df) and len(current_ids) < self.max_reviews_at_once:
            current_id = self.due_review_ids.pop()
            current_ids.add(current_id)

        current_df = sorted_df[sorted_df["ID"].isin(current_ids)]
        items = current_df.to_dict("records")
        self.add_to_review(items)

        return self.current_reviews

    # if the user has not designated to stop reviewing, get another item and add it to the review list
    @check_conn
    def update_review_session(self) -> None:

        # stop we have already added all review items into our list, so we can stop updating review
        if len(self.due_review_ids) == 0:
            self.stop_updating_review = True

        if not self.stop_updating_review:

            # adds one id
            current_id = self.due_review_ids.pop()
            q = f"""
                SELECT * FROM {self.name_srs_table}
                WHERE {self.col_dict["id_col"]} = {current_id};
                """
 
            df = pd.read_sql_query(q, self.conn)
            item = df.to_dict("records")
            self.add_to_review(item)

        return None

    # defines an item and adds it to the review list
    @check_conn
    def add_to_review(self, items: list) -> None:
        for item in items:
            review_type = None
            current_item = None

            kanji_item = item.get(self.col_dict["kanji_col"])
            vocab_item = item.get(self.col_dict["vocab_col"])

            if kanji_item:
                review_type = "kanji"
                current_item = kanji_item

            elif vocab_item:
                review_type = "vocab"
                current_item = vocab_item

            # we need to make two cards: reading and meaning
            # they are defined as such
            reading_card = item.copy()
            reading_card["review_type"] = review_type
            reading_card["card_type"] = "reading"
            reading_card["prompt"] = current_item
            reading_card["expected_answer"] = reading_card["Readings"]
            self.current_reviews.append(reading_card)

            meaning_card = item.copy()
            meaning_card["review_type"] = review_type
            meaning_card["card_type"] = "meaning"
            meaning_card["prompt"] = current_item
            meaning_card["expected_answer"] = meaning_card["Meanings"]
            self.current_reviews.append(meaning_card)

        random.shuffle(self.current_reviews)

        return None

    # adds another valid meaning to the item in the db
    @check_conn
    def add_valid_response(self, user_input: str, item: dict) -> None:
        card_type = item["card_type"]
        item_id = item["ID"]

        match card_type:
            case "reading":
                response_col = "Readings"

            case "meaning":
                response_col = "Meanings"

        q = f"""
            UPDATE {self.name_srs_table}
            SET
                {response_col} = ?
            WHERE {self.col_dict["id_col"]} = {item_id};
            """

        valid_responses = item[response_col]
        valid_responses += f",{user_input}"

        self.conn.execute(q, (valid_responses,))
        self.to_commit()

        return None

    # adds an item from the vocab/kanji db to the srs review db
    @check_conn
    def add_review_item(self, item: dict) -> None:
        q = f"""
            INSERT INTO {self.name_srs_table} (Meanings, Readings, CurrentGrade, FailureCount, SuccessCount, AssociatedVocab, AssociatedKanji, MeaningNote, ReadingNote, Tags, IsDeleted, LastUpdateDateISO, CreationDateISO, NextAnswerDateISO)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """

        # utc current timestamp
        current_datetime = datetime.now(timezone.utc)
        next_answer_datetime = current_datetime + timedelta(hours = self.srs_interval["0"]["value"])

        # default definitions
        # timestamp as such for both readability and debugging
        meanings = item["meanings"].value
        readings = item["readings"].value
        current_grade = 0
        failure_count = 0
        success_count = 0
        associated_vocab = None
        associated_kanji = None
        meaning_notes = item["meaning_notes"].value
        reading_notes = item["reading_notes"].value
        tags = None
        is_deleted = 0
        last_update_date = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        creation_date = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        next_answer_date = next_answer_datetime.strftime("%Y-%m-%d %H:%M:%S")

        match item["type"]:
            case "vocab":
                associated_vocab = item["kanji"].value

            case "kanji":
                associated_kanji = item["kanji"].value

        # big tuple...
        self.conn.execute(q, (meanings, readings, current_grade, failure_count, success_count, associated_vocab, associated_kanji, meaning_notes, reading_notes, tags, is_deleted, last_update_date, creation_date, next_answer_date))
        self.conn.commit()

        return None

    # after an answer has been processed, edit the item's status in the db
    @check_conn
    def update_review_item(self, item_id: str, res: bool) -> None:
        q_retrieve_item = f"""
                          SELECT 
                              CurrentGrade,
                              FailureCount,
                              SuccessCount
                          FROM {self.name_srs_table}
                          WHERE {self.col_dict["id_col"]} = {item_id};
                          """
        q_update_item = f"""
                        UPDATE {self.name_srs_table}
                        SET
                            CurrentGrade = ?,
                            FailureCount = ?,
                            SuccessCount = ?,
                            LastUpdateDateISO = current_timestamp,
                            NextAnswerDateISO = ?
                        WHERE {self.col_dict["id_col"]} = {item_id};
                        """

        df = pd.read_sql_query(q_retrieve_item, self.conn)
        row = df.to_dict("records")[0]

        # utc current timestamp
        current_time = datetime.now(timezone.utc)

        # if the user got the item correct, increase the grade and success count
        # otherwise, opposite
        if res:
            row["CurrentGrade"] += 1
            row["SuccessCount"] += 1

        else:
            row["CurrentGrade"] = max(0, row["CurrentGrade"] - 1)
            row["FailureCount"] += 1

        current_grade_key = str(row["CurrentGrade"])
        current_grade_dict = self.srs_interval[current_grade_key]

        # if -1, then the user has proved that they know this item well enough to stop reviewing
        # otherwise, use the toml to determine when the next review date is
        match current_grade_dict["value"]:
            case -1:
                review_time = None

            case _:
                match current_grade_dict["unit"]:
                    case "hours":
                        review_datetime = current_time + timedelta(hours = current_grade_dict["value"])

                    case "days":
                        review_datetime = current_time + timedelta(days = current_grade_dict["value"])

                review_time = review_datetime.strftime("%Y-%m-%d %H:%M:%S")

        self.conn.execute(q_update_item, (row["CurrentGrade"], row["FailureCount"], row["SuccessCount"], review_time))
        self.current_completed += 1 # increment counter for frontend
        self.to_commit()

        return None

    # after user edits an item, we should change its respective variables
    @check_conn
    def edit_review_item(self, item: dict) -> None:
        q = f"""
            UPDATE {self.name_srs_table}
            SET
                Meanings = ?,
                Readings = ?,
                CurrentGrade = ?,
                AssociatedVocab = ?,
                AssociatedKanji = ?,
                MeaningNote = ?,
                ReadingNote = ?,
                LastUpdateDateISO = current_timestamp,
                NextAnswerDateISO = ?
            WHERE {self.col_dict["id_col"]} = {item["item_id"]};
            """

        # default definitions
        # timestamp as such for both readability and debugging
        meanings = item["meanings"].value
        readings = item["readings"].value
        current_grade = item["current_grade"].value
        associated_vocab = None
        associated_kanji = None
        meaning_notes = item["meaning_notes"].value
        reading_notes = item["reading_notes"].value
        next_answer_date = item["next_answer"].value

        match item["type"]:
            case "vocab":
                associated_vocab = item["kanji"].value

            case "kanji":
                associated_kanji = item["kanji"].value

        # big tuple...
        self.conn.execute(q, (meanings, readings, current_grade, associated_vocab, associated_kanji, meaning_notes, reading_notes, next_answer_date))
        self.conn.commit()

        return None

    # function to convert db from houhou
    # specifically, this just adds similar columns representing time but in iso format for readability
    @check_conn
    def convert_from_houhou(self) -> None:
        names_date_col = [
            self.col_dict["houhou_last_update_col"],
            self.col_dict["houhou_creation_col"],
            self.col_dict["houhou_next_answer_col"],
            self.col_dict["houhou_suspension_col"],
        ]

        for name_col in names_date_col:
            name_iso_col = name_col + "ISO"
            q_create_col = f"ALTER TABLE {self.name_srs_table} ADD COLUMN {name_iso_col} TEXT;"
            q_update_col = f"""
                           UPDATE {self.name_srs_table}
                           SET {name_iso_col} = 
                               CASE 
                                   WHEN typeof({name_col}) = 'text' 
                                   AND {name_col} GLOB '20[0-9][0-9]-*' THEN 
                                       {name_col}

                                   WHEN typeof({name_col}) = 'integer' THEN
                                       datetime(({name_col} / 10000000) - 62135596800, 'unixepoch')

                                   ELSE NULL
                               END;
                           """

            try:
                self.conn.execute(q_create_col)
                self.conn.execute(q_update_col)

                self.conn.commit()

            # don't raise the error
            # most likely it's just saying the column exists if you run this function multiple times
            except Exception as e:
                print(f"{name_col}: {e}")

                continue

        return None
