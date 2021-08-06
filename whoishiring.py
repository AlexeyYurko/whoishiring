"""
get job listing from "Who is hiring" HN thread
TODO remake job_head and job_description - for now it's a mess and generate
    a ton of crazy html-like code with incorrect close/open tags
TODO allow to modify keywords via command line arguments or config file
TODO remake block for making html entries
"""
import argparse
import codecs
import datetime
import pickle
import re
import sys
from concurrent.futures import ThreadPoolExecutor
import time

import requests
from pymongo import MongoClient
from pymongo.errors import AutoReconnect
from tqdm import tqdm


def create_parser():
    """create_parser for argparse

    Returns:
        object: parser
    """

    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "-t", "--thread", action="store", help="Who is hiring thread number"
    )
    return argparser


def get_item_url(kid_id):
    """
    make url with kid_id
    """
    return f"https://hacker-news.firebaseio.com/v0/item/{kid_id}.json"


def get_thread_name(from_thread_id):
    """
    extract name of the thread + month and year
    """
    try:
        story_name = requests.get(get_item_url(from_thread_id)).json()["title"]
    except TypeError:
        print(f"Thread {from_thread_id} non exist.")
        sys.exit()
    if "right now" in story_name:
        return "whoishiring right now"
    month_year = re.findall(r"\(([A-Za-z]+ \d+)\)", story_name)[0].lower()
    return "_".join(f"whoishiring {month_year}".split(" ")), month_year


def get_kids(thread_id_to_get_kids):
    """
    get kids from story thread
    """
    return requests.get(get_item_url(thread_id_to_get_kids)).json()["kids"]


def get_multi_comments(kid):
    """
    get comment from kid_id, as multiprocessor call
    """
    client = MongoClient()
    database = client["whoishiring"]
    jobs = database["jobs"]
    result = requests.get(get_item_url(kid)).json()
    next_comment = result["text"] if result and "text" in result else ""
    if next_comment:
        comment_time = datetime.datetime.fromtimestamp(int(result["time"])).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        comment_time_date, comment_time_time = comment_time.split(" ")
        job_head = next_comment.split("<p>")[0]
        job_description = "<br>".join(next_comment.split("<p>")[1:]).replace("</p>", "")
        try:
            jobs.insert_one(
                {
                    "kid": kid,
                    "head": job_head,
                    "description": job_description,
                    "day": comment_time_date,
                    "time": comment_time_time,
                }
            )
        except AutoReconnect as e:
            time.sleep(0.5)
    client.close()


def grab_new_comments(all_kids, month_year):
    """
    get saved kid_id from base, get only new id with multiprocessing
    """
    client = MongoClient()
    database = client["whoishiring"]
    jobs = database["jobs"]
    kids_in_base = {record["kid"] for record in jobs.find({}, {"kid": 1})}
    kids_to_add = {kid for kid in all_kids if kid not in kids_in_base}
    with ThreadPoolExecutor() as executor:
        _ = list(
            tqdm(executor.map(get_multi_comments, kids_to_add), total=len(kids_to_add))
        )

    comments = [
        {
            "kid": comment["kid"],
            "head": comment["head"],
            "description": comment["description"],
            "day": comment["day"],
            "time": comment["time"],
            "month_year": month_year,
        }
        for comment in jobs.find({})
    ]
    comments = sorted(comments, key=lambda x: x["time"], reverse=True)
    client.close()
    return comments


def make_html(job_listing, filename, month_year):
    """
    create simple html from comments with (and without) keyword
    """
    counter = 0
    with open("template.html", "r") as file:
        template = file.read()
    jobs_block = ""
    for i, entry in enumerate(job_listing, 1):
        entry_text = f"{entry['head']} {entry['description']}"
        if entry["month_year"] != month_year:
            continue
        if "remote" not in entry_text.lower():
            continue
        block_start = '<div class="job_entry">'
        first_line = f"""<div class="job_head"><em>#{i}</em>
                            {entry['head']}, posted: {entry['day']} at {entry['time']}</div>"""
        jobs_block += f"""{block_start}{first_line}
                            {entry['description']}</a></div>\n"""
        counter += 1
    with codecs.open(f"{filename}.html", "w", encoding="utf-8") as file:
        file.write(template.format(filename, jobs_block))
    print(f"Written to html: {counter} job postings.")


def write_thread_id(thread_id_to_save):
    """write_thread_id Saves last used thread_id in pickle

    Args:
        thread_id_to_save (int): thread_id to save

    Warning: No check if thread doesn't exist
    TODO make check for thread
    """

    with open("last_thread.pickle", "wb") as pickle_out:
        pickle.dump(thread_id_to_save, pickle_out)


def run(thread):
    """
    main block for getting data with API
    """
    name, month_year = get_thread_name(thread)
    kids = get_kids(thread)
    print(f'In thread {thread} with name "{name}" are {len(kids)} records')
    new_comments = grab_new_comments(kids, month_year)
    make_html(new_comments, name, month_year)


if __name__ == "__main__":
    PARSER = create_parser()
    ARGS = PARSER.parse_args(sys.argv[1:])

    if not ARGS.thread:
        try:
            # get last used thread_id
            with open("last_thread.pickle", "rb") as pickle_in:
                THREAD_ID = pickle.load(pickle_in)
        except FileNotFoundError:
            sys.exit(f"Syntax: {sys.argv[0]} -t <thread_id>")
    else:
        THREAD_ID = ARGS.thread

    write_thread_id(THREAD_ID)
    run(THREAD_ID)
