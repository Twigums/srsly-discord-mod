import os
import discord
import re
import random
import pandas as pd

from enum import Enum, auto
from discord.ext import commands
from typing import Optional
from pyokaka import okaka
from rapidfuzz import process, fuzz

from src.dataclasses import BotConfig, Colors, Card


def romaji_to_kana(string):
    processed_string = ""
    prev = ""

    for char in string.lower():
        if char + prev == "nn":
            prev = ""
            processed_string += "n'"

        else:
            prev = char
            processed_string += char

    kana = okaka.convert(processed_string)

    return kana

class AppState(Enum):
    RUNNING = auto()
    WILL_STOP = auto()
    STOPPED = auto()

class Bot:
    def __init__(self, config: BotConfig, colors: Colors):
        intents = discord.Intents.default()
        intents.message_content = True

        self.command_prefix = config.prefix

        self.bot = commands.Bot(
            command_prefix = self.command_prefix,
            intents = intents
        )

        self.srs_app = config.srs_app
        self.debug_mode = config.debug

        # init defs
        self.token = config.token
        self.colors = colors
        self.current_card = Card()

        self.review_channel = None
        self.item_dict = None
        self.showing_wrong_message = False
        self.previous_answer = None
        self.state = AppState.STOPPED

        # thanks claude
        self.encouraging_messages = [
            "You got it next time! 💪",
            "Failure is just practice in disguise! 🎯",
            "Plot twist: this was just the warm-up! 🔥",
            "Every expert was once a beginner who didn't quit! 🌟",
            "Oops is just the sound of learning! 🚀",
            "Consider this: valuable data collected! 📊",
            "The comeback is always stronger! 💥",
            "You're one attempt closer to success! ⭐",
            "Mistakes are proof you're trying! 🎪",
            "This is just your origin story! 🦸",
            "Failing forward like a pro! 🏃‍♂️",
            "Today's L is tomorrow's lesson! 📚",
            "You're building character right now! 🏗️",
            "Plot armor activated for next time! 🛡️",
            "The only real failure is not trying again! 🎲",
            "Leveling up through experience points! 🎮",
            "This is how legends are made! 👑",
            "Your persistence is showing! 💎",
            "Failure: the secret ingredient to success! 🧪",
            "Round two is gonna hit different! 🥊"
        ]

        self.setup_events()
        self.setup_commands()

    def _start_review(self) -> bool:
        reviews = self.srs_app.start_review_session()

        # initialize empty vars
        self.item_dict = dict()

        if reviews:
            return True

        else:
            return False

    def _clean_buffer(self) -> None:
        self.showing_wrong_message = False
        self.previous_answer = None

        return None

    def start(self) -> None:
        self.bot.run(self.token)

        return None

    def update_embed(self):
        previous_text = self.current_card.kanji or self.current_card.vocab
        current_item = self.srs_app.get_current_item()

        if current_item is None:

            # set everything back to default
            self.state = AppState.STOPPED
            self.review_channel = None
            self.srs_app.force_commit()

            return discord.Embed(title = "No more reviews!")

        self.current_card.review_type = current_item["review_type"]
        self.current_card.card_type = current_item["card_type"]
        self.current_card.item_id = current_item["ID"]
        self.current_card.readings = current_item["Readings"]
        self.current_card.meanings = current_item["Meanings"]
        self.current_card.kanji = current_item["AssociatedKanji"]
        self.current_card.vocab = current_item["AssociatedVocab"]

        review_color = None
        separator = None
        display_text = None

        # style the cards differently based on what the item is
        match self.current_card.review_type:
            case "kanji":
                display_text = self.current_card.kanji
                review_color = self.colors.kanji

            case "vocab":
                display_text = self.current_card.vocab
                review_color = self.colors.vocab

        match self.current_card.card_type:
            case "reading":
                separator = ":black_large_square:" * 10

            case "meaning":
                separator = ":white_large_square:" * 10

        embed = discord.Embed(
            title = display_text,
            description = separator,
            color = discord.Color.from_rgb(review_color[0], review_color[1], review_color[2])
        )
        embed.set_footer(text = f"{self.srs_app.current_completed} / {self.srs_app.len_review_ids}")

        return embed

    def wrong_embed(self, content, correct_readings):
        match self.current_card.review_type:
            case "kanji":
                display_text = self.current_card.kanji

            case "vocab":
                display_text = self.current_card.vocab

        match self.current_card.card_type:
            case "reading":
                separator = ":black_large_square:" * 10
                user_response = romaji_to_kana(content)

            case "meaning":
                separator = ":white_large_square:" * 10
                user_response = content

        embed = discord.Embed(
            title = display_text,
            description = separator,
            color = discord.Color.brand_red()
        )

        embed.set_footer(text = f"{self.srs_app.current_completed} / {self.srs_app.len_review_ids}")

        embed.add_field(
            name = "Correct readings:",
            value = correct_readings,
            inline = False
        )

        embed.add_field(
            name = ":x:",
            value = user_response,
            inline = False
        )

        return embed

    # function to process an answer and calls the app to save the information
    def process_answer(self, answer, will_submit):
        answer_stripped = answer.strip()
        answer_lower = answer_stripped.lower()
        answer_kana = None
        lookup_readings = dict()

        # keep track of progress for all items using a dictionary
        if self.current_card.item_id not in self.item_dict:
            self.item_dict[self.current_card.item_id] = []

        # retrieve all valid readings and compare the typed answer to the valid readings
        match self.current_card.card_type:

            # reading cards should be strict, since a mistype of kana usually means a different word
            case "reading":
                valid_readings = self.current_card.readings.split(",")
                answer_kana = romaji_to_kana(answer_lower)

                for reading in valid_readings:
                    reading_stripped = reading.strip()
                    lookup_readings[reading_stripped] = reading

                if answer_kana in lookup_readings:
                    matching_score = 100

                else:
                    matching_score = 0

            # use fuzzy matching to score meanings
            case "meaning":
                valid_readings = self.current_card.meanings.split(",")

                for reading in valid_readings:
                    reading_stripped = reading.strip()
                    reading_lower = reading_stripped.lower()
                    remove_all_in_parentheses = re.sub(r"\s*\([^)]*\)\s*", "", reading_lower)
                    strip_parentheses = re.sub(r"[()]", "", reading_lower)

                    lookup_readings[strip_parentheses] = reading
                    lookup_readings[remove_all_in_parentheses] = reading

                _, matching_score, _ = process.extractOne(answer_lower, lookup_readings.keys(), scorer = fuzz.QRatio)

        valid_readings_str = str(valid_readings)
        self.previous_answer = answer_kana if answer_kana else answer_lower

        # if the score is over a certain threshold, then we mark it as correct
        # otherwise, it's incorrect
        current_review = self.srs_app.current_reviews.pop(self.srs_app.current_index)

        to_append = 0
        if matching_score > self.srs_app.match_score_threshold:
            to_append = 1

        else:
            self.srs_app.current_reviews.append(current_review)

        if to_append == 1 or will_submit:
            self.item_dict[self.current_card.item_id].append(to_append)

            # my way of marking if both the reading and meaning cards are marked as correct
            # if so, then we should update the review item
            # if the user gets both correct on the first try, the list would look like [1, 1]
            # if they can't something wrong: [..., 1, ..., 1], where ... may be any length of 0s
            # a faster solution is storing a tuple (a, b)
            # if a = 2, then the user has completed both reviews
            # b is a counter for how many tries the user has taken
            if sum(self.item_dict[self.current_card.item_id]) == 2:
                if len(self.item_dict[self.current_card.item_id]) == 2:
                    self.srs_app.update_review_item(self.current_card.item_id, True)

                else:
                    self.srs_app.update_review_item(self.current_card.item_id, False)

                del self.item_dict[self.current_card.item_id]
                self.srs_app.update_review_session()

        return (bool(to_append), valid_readings_str)

    def setup_events(self):

        @self.bot.event
        async def on_ready() -> None:
            if self.debug_mode:
                print(f"{self.bot.user} is connected.")

            return None

        @self.bot.event
        async def on_message(message) -> None:
            author = message.author
            content = message.content

            # don't listen to self msgs
            if author == self.bot.user:
                return None

            # if review is active in this channel, print res
            if self.state in [AppState.RUNNING, AppState.WILL_STOP] and message.channel == self.review_channel:
                embed = None

                # term msgs
                if self.debug_mode:
                    print(f"[{author}]: {content}")

                if self.showing_wrong_message:
                    match content:
                        case "ok":
                            await message.channel.send(random.choice(self.encouraging_messages))
                            _, correct_readings = self.process_answer(self.previous_answer, True)
                            embed = self.update_embed()
                            self._clean_buffer()

                        case "add":
                            await message.channel.send(f"Added {self.previous_answer} as a valid response.")

                            current_item = {
                                "card_type": self.current_card.card_type,
                                "ID": self.current_card.item_id,
                                "Readings": self.current_card.readings,
                                "Meanings": self.current_card.meanings
                            }

                            # same as srsly i guess...
                            self.srs_app.add_valid_response(self.previous_answer, current_item)
                            self.srs_app.current_reviews.pop()
                            
                            self.item_dict[self.current_card.item_id].append(1)

                            # please for the love of god make this more optimized
                            if sum(self.item_dict[self.current_card.item_id]) == 2:
                                if len(self.item_dict[self.current_card.item_id]) == 2:
                                    self.srs_app.update_review_item(self.current_card.item_id, True)
                                else:
                                    self.srs_app.update_review_item(self.current_card.item_id, False)
                                
                                del self.item_dict[self.current_card.item_id]
                                self.srs_app.update_review_session()

                            embed = self.update_embed()
                            self._clean_buffer()

                        case "re":
                            await message.channel.send("redo")
                            embed = self.update_embed()
                            self._clean_buffer()

                        case _:
                            await message.channel.send("Please type either 'ok', 'add', or 're'.")

                    if embed:
                        await message.channel.send(embed = embed)

                    return None

                # don't process any commands
                elif content.startswith(self.command_prefix):
                    await self.bot.process_commands(message)

                    return None

                # "otherwise"
                else:

                    # will set self.previous_answer to content (either in kana or processed)
                    is_correct, correct_readings = self.process_answer(content, False)
    
                    if is_correct:
                        await message.channel.send(":o:")
                        await message.channel.send(correct_readings)
                        embed = self.update_embed()
    
                    else:
                        embed = self.wrong_embed(content, correct_readings)
                        self.showing_wrong_message = True
    
                    await message.channel.send(embed = embed)

            if self.debug_mode:
                print(self.item_dict)

            await self.bot.process_commands(message)

            return None

    def setup_commands(self) -> None:

        # self test
        @self.bot.slash_command(name = "ping", description = "Check if the bot is alive.")
        async def ping(ctx: commands.Context) -> None:
            await ctx.respond("pong")

            return None

        # "start" should first check if we have stuff in our queue
        # if we don't then kick the user out
        # if we did, then keep going
        @self.bot.slash_command(name = "start", description = "Start a review session.")
        async def start_review(ctx: commands.Context) -> None:
            
            # don't run if app is already started
            if self.state == AppState.RUNNING:
                await ctx.respond("Already started...")

                return None

            if not self._start_review():
                await ctx.respond("No reviews!")

                return None

            self.update_embed()

            self.review_channel = ctx.channel
            self.state = AppState.RUNNING

            await ctx.respond("Review session started!")
            await ctx.send(f"You have **{self.srs_app.len_review_ids}** reviews due.")
            await ctx.send("Type `/stop` to end the session.")

            embed = self.update_embed()

            await ctx.channel.send(embed = embed)

            return None

        # "stop" should issue a command to stop pushing stuff into the queue
        @self.bot.slash_command(name = "stop", description = "Stop current review session.")
        async def stop_review(ctx: commands.Context) -> None:

            # if app is either flagged to stop or is stopped, then no point in stopping
            if self.state in [AppState.WILL_STOP, AppState.STOPPED]:
                await ctx.respond("Already will/has stopped.")

                return None

            self.srs_app.stop_updating_review = True
            self.state = AppState.WILL_STOP

            await ctx.respond("Will quit after the remaining items are completed.")

            return None

        # "stats" should show important stats to the user
        @self.bot.slash_command(name = "stats", description = "Show stats of current deck.")
        async def show_stats(ctx: commands.Context) -> None:

            # im not gonna make these a config...
            # there are 5 different "levels" old wanikani and houhou have
            level_names = ["Discovering", "Committing", "Bolstering", "Assimilating", "Set in Stone"]
            level_grades = [[0, 1], [2, 3], [4, 5], [6, 7], [8]]

            df_grade_counts, df_today_counts, df_ratio = self.srs_app.get_review_stats()
            grade_values = df_grade_counts.iloc[:, -1].tolist()

            df_reviews = self.srs_app.get_due_reviews()

            if grade_values == []:
                await ctx.respond("Start adding items and reviewing to see stats!")

                return None

            # to prevent "failed interaction" when using slash commands
            await ctx.defer()

            for name, color, grades in zip(level_names, self.colors.progress, level_grades):
                embed = discord.Embed(
                    title = name,
                    description = sum([grade_values[grade] for grade in grades]),
                    color = discord.Color.from_rgb(color[0], color[1], color[2])
                )

                await ctx.send(embed = embed)

            embed = discord.Embed(
                title = "# of Reviews Due",
                description = f"{len(df_reviews)} / {df_today_counts.values[0][0]}",
                color = discord.Color.from_rgb(55, 55, 62) # discord's ash embed
            )

            ratio = df_ratio.values.item()
            embed.set_footer(text = f"So far, you got {(ratio * 100):.2f} correct.")

            await ctx.send(embed = embed)

            return None

        return None