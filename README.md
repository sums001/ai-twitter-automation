# OpenX

**OpenX automates your X (Twitter) account for you.**

It reads the latest news and posts from across the web on the topics you care about, uses AI to write original posts in your own style, adds a picture, and posts them for you on a natural, human looking schedule. You run everything from a simple dashboard in your browser. No coding needed.

You always stay in control. OpenX starts in a safe practice mode where it only shows you what it would post. It posts for real only when you decide you are ready.

## What it does for you

* **Finds content.** It gathers fresh stories from many places on the web so you never run out of things to post about.
* **Stays on your topics.** You pick the subjects you want to post about, and it only uses content that fits.
* **Removes the junk.** It drops spam, off topic stories, old news, and the same story repeated in different places.
* **Writes the post.** AI turns a story into a short, original post in the voice and tone you choose.
* **Adds a picture.** It attaches a fitting image when the post calls for one.
* **Posts like a person.** It spreads your posts through the day at slightly random times so it never looks like a bot.
* **Lets you approve first.** You can review and edit every draft before it goes out, or get them on Telegram to approve with one tap.
* **Keeps you safe.** It stays in practice mode until you go live, and it never posts more than the daily limit you set.
* **Shows you everything.** A dashboard shows what is queued, what was posted, your costs, and every setting you can change.

## The topics you can post about

OpenX comes with 18 ready made topics, called niches. You can turn any of them on or off, and change how each one sounds. The topics are:

* Technology
* Artificial Intelligence
* Crypto
* Finance
* Startups
* Business
* Marketing
* Science
* Health
* Fitness
* Self improvement
* Education
* News
* Politics
* Sports
* Gaming
* Entertainment
* Lifestyle

Tip: a few focused topics give better results than turning on all of them at once.

## Where it gets content

Each topic pulls from a mix of trusted places on the web. Across all topics, OpenX can read from:

* **Google News**, for the latest headlines on any subject
* **Reddit**, for popular community posts
* **YouTube**, for videos and their transcripts
* **Hacker News**, for technology and startup discussion
* **Medium** and other **blogs and news sites** (TechCrunch, Ars Technica, Futurism, CoinDesk, Decrypt, The Block, MarketWatch, CNBC, Harvard Business Review, Fast Company, Fortune, Entrepreneur, the Y Combinator blog, Nature, Phys.org, ScienceDaily, and the World Health Organization)
* **Dev.to**, for developer articles
* **arXiv**, for research papers
* **The Guardian**, for world news
* **Yahoo Finance**, for market and stock news

You can add any other news site or blog that has an RSS feed on the **Sources** page.

Almost all of these work right away with no setup. YouTube and The Guardian need a free key, which is explained below. The **Sources** page groups sources into "No key needed" and "Needs a key".

## What you need before you start

You need two free programs on your computer:

1. **Python**, version 3.12 or newer, from https://www.python.org/downloads/
   On Windows, tick the box that says **Add Python to PATH** while installing.
2. **Node.js**, from https://nodejs.org (click the green LTS button).

You do not need to know how to code.

## How to install and run it

1. Download this project into a folder on your computer.
2. Open a terminal in that folder.
   * On Windows: open the folder in File Explorer, type `cmd` in the address bar, and press Enter.
   * On Mac: right click the folder and choose New Terminal at Folder.
3. Type this and press Enter:

   ```
   python setup.py
   ```

   On Mac, use `python3 setup.py` if `python` does not work.

The first time you run it, OpenX sets everything up for you. This takes a few minutes. After that it starts in seconds.

4. When it says it is ready, open your browser and go to:

   **http://127.0.0.1:5174**

You are in.

To stop OpenX, go back to the terminal and press the **Ctrl** key and the **C** key together. To start it again any time, run `python setup.py` again.

## A quick tour of the dashboard

Across the top or side you will find these pages:

* **Dashboard**, a summary of what is happening.
* **Niches**, where you choose your topics and how they sound.
* **Queue**, where you see and manage draft posts.
* **Schedule**, where you set when and how often to post.
* **Sources**, where you turn content sources on or off and add their keys.
* **History**, where you see what was posted and what it cost.
* **Logs**, a running record of activity.
* **Settings**, where you control practice mode, your voice samples, limits, and your account keys.
* **Raw Data**, where you can see the stories OpenX has gathered and which one it would post about next.

## Setting up your topics

1. Open the **Niches** page.
2. Turn on the topics you want to post about.
3. For any topic, you can change the **voice and tone**, for example "friendly and casual" or "sharp and professional". This is what shapes how your posts read.
4. Save your changes.

Only the topics you turn on are used. The rest are ignored.

## Teaching OpenX your voice

On the **Settings** page, under **Voice samples**, paste three to five real posts that sound the way you want your account to sound. OpenX copies their style (short paragraphs, casual wording, the way lines break), never their topics. It comes with a few example posts so it works straight away. A topic can have its own samples too, on the **Niches** page under "Voice override".

## How OpenX picks what to post

Everything your sources bring in goes into one shared pool of stories. When several sites report the same news, OpenX treats it as one story and sees that it is big news. Before each post it ranks the stories it hasn't used yet (fresh, widely covered, on topic, not a repeat of what you just posted), reads the full article for the winner, and writes one post. A story is never posted twice from the same account. This ranking costs nothing; each post uses only one or two small AI requests.

## Choosing the AI that writes your posts

OpenX uses AI to write each post. You choose which AI on the **Niches** page, under the writing settings for each topic.

* **Free option on your computer.** Out of the box, OpenX is set to use a free AI that runs on your own machine. To use it, install **Ollama** from https://ollama.com and download a model once. This costs nothing to run.
* **Paid options.** You can instead use a well known AI such as Claude, ChatGPT, or Gemini. To do this, enter the model name in the topic settings and add your key on the **Settings** page (see the next section). These give the highest quality writing but the provider charges for use.

If no AI is available, OpenX still creates simple drafts so you can try the app, but the writing will be basic until you set up a real model.

## Adding your keys

A key is like a password that lets OpenX use an outside service. You add all keys on the **Settings** page (and source keys on the **Sources** page). Your keys are encrypted and stored only on your own computer. They are never shown back to you after you save them.

You may want these keys:

* **An AI key**, only if you chose a paid AI like Claude, ChatGPT, or Gemini. You add it on the **Settings** page.
* **An image key (Unsplash)**, only if you want stock photos attached to posts. You add it on the **Settings** page, alongside your AI keys. Without it, topics set to use Unsplash photos simply post without an image.
* **Source keys**, only for the few sources that need one. These are YouTube and The Guardian. Everything else works without a key.
* **Your X account keys**, when you are ready to post for real (see below).

Your AI key, image key, and X account keys belong to the workspace you are in, so different workspaces can use different keys. To add a source key, open the **Sources** page, find the source, paste its key, turn the source on, and save (source keys are shared across all workspaces).

## Connecting your X account

To post for real, OpenX needs permission to use your X account. From X you get four values, usually called the API key, API secret, access token, and access token secret. Paste these into the X account section on the **Settings** page and save. They are encrypted and kept on your computer only.

Until you connect an account, OpenX stays in practice mode and never posts.

## Setting your posting schedule

Open the **Schedule** page to decide when and how often your account posts. You can set:

* **The posting window**, the hours of the day it is allowed to post, for example 9am to 9pm.
* **Post every**, how often to post inside that window, for example every 45 minutes.
* **Randomness**, how much each gap varies, so your account looks natural rather than automated.
* **Minimum gap**, a safety limit so two posts are never too close together.

The page shows when the next post will go out and what was posted today. About five minutes before each post, OpenX checks your sources for fresh news so the post is up to date.

## Practice mode and going live

OpenX has a safety switch on the **Settings** page.

* **Practice mode (the default).** OpenX does everything except actually post. It shows you what it would have posted. Use this to get comfortable.
* **Live mode.** When you are ready, connect your X account, then turn practice mode off. Now OpenX posts for real.

There is also a choice between two ways of working:

* **Manual.** OpenX writes a draft on your schedule and waits for you. Nothing goes out until you say so. Once a few drafts are waiting, it pauses so it doesn't use AI for posts you haven't looked at.
* **Automatic.** OpenX posts on its own on your schedule.

Start in practice mode and manual. Move to live and automatic only when you trust the results.

## Approving posts before they go out

If you like to check posts first, you have two easy ways:

* **In the dashboard.** Open the **Queue** page to read each draft. You can edit the words, ask for a fresh version, or reject it.
* **On Telegram.** You can have new drafts sent to your Telegram, where you approve or reject each one with a tap. If you do not respond in time, OpenX follows the rule you set, either posting it or skipping it.

## Staying safe

* **Daily limit.** You can set a top number of posts per day across all topics, so OpenX can never post too much.
* **Your keys are private.** Everything you enter is encrypted and stays on your computer.
* **Nothing posts by accident.** Practice mode is on until you turn it off yourself.

## Good to know about cost

* **Running OpenX is free.** The free local AI option costs nothing to run.
* **Some choices may cost money.** Using a paid AI, or posting through X, may carry small charges from those providers. The dashboard tracks your posting costs so there are no surprises.

## Stopping and starting again

* **To stop:** press the **Ctrl** key and the **C** key together in the terminal.
* **To start again later:** run `python setup.py` again in the project folder, then open http://127.0.0.1:5174.

## For developers

The technical design and inner workings are documented separately in [project.md](project.md) and [CLAUDE.md](CLAUDE.md).
