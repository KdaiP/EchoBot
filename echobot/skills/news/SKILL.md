---
name: news
description: >
  Fetch the latest news and headlines from authoritative Chinese and international
  sources. Use this skill whenever the user asks about news, headlines, hot topics,
  breaking news, or current events — even casual phrasing like "今天有什么新闻？",
  "最新科技动态", "财经热点", "体育新闻", "娱乐八卦", "国际新闻", "what's in the
  news?", "any big stories today?". Covers politics, finance, tech, society, world,
  sports, and entertainment. Always use this skill for news-related requests, even
  if the user doesn't say the word "news" explicitly.
metadata:
  echo: '{"emoji": "📰"}'
---

# News

Fetch and present the latest headlines using the **fetch_web_page** tool.

## Categories and Sources

| Category              | Primary                                                     |
|----------------------|-------------------------------------------------------------|
| **综合 / General**    | 新华网 https://www.xinhuanet.com/                           |
| **政治 / Politics**   | 人民网·中共新闻网 https://cpc.people.com.cn/               |
| **财经 / Finance**    | 中国经济网 http://www.ce.cn/                               |
| **社会 / Society**    | 中国新闻网·社会 https://www.chinanews.com/society/         |
| **国际 / World**      | CGTN https://www.cgtn.com/                                 |
| **科技 / Tech**       | 科技日报 https://www.stdaily.com/                          |
| **体育 / Sports**     | CCTV体育 https://sports.cctv.com/                          |
| **娱乐 / Entertainment** | 新浪娱乐 https://ent.sina.com.cn/                    |

## Workflow

### 1. Identify what the user wants

- **Single category**: fetch that category's URL.
- **Multiple categories**: fetch all relevant URLs in parallel (call `fetch_web_page` multiple times in the same response).
- **No category specified**: default to General (新华网) or ask if ambiguous.

### 2. Fetch the page

Call `fetch_web_page` with a generous `max_chars` to capture enough headlines:

```json
{
  "url": "https://36kr.com/",
  "max_chars": 10000
}
```

Use `max_chars` between **8000–12000** for news homepages — smaller values cut off too many headlines.

### 3. Extract headlines

From the returned text, look for repeating patterns: headline titles, brief descriptions, timestamps. Most Chinese news sites return plain-text content with clear headline structures after HTML is stripped.

Focus on:
- The **headline** (title of the story)
- A **one or two sentence summary** (from the teaser or first paragraph)
- The **date/time** if visible in the text
- The **source name**

### 4. Present results

Format your reply like this (adapt to Chinese or English based on the user's language):

---

**📰 [Category] 最新资讯** — *来源：[Source Name]*

1. **[Headline]**
   [One or two sentence summary.] *(date/time if available)*

2. **[Headline]**
   [Summary.]

*(more headlines...)*

🔗 查看完整报道：[Source URL]

---

- List **5–10 headlines** per category. More is fine if the user asked for an overview.
- If the user asked for multiple categories, present each as a separate section.
- Match the user's language: reply in Chinese if they asked in Chinese, English if in English.

## Error handling

| Situation | What to do |
|-----------|-----------|
| Site times out or returns an error | Try the fallback URL from the table |
| Content looks like JS placeholders (no readable text) | Use the fallback; mention the site may need a browser |
| Both primary and fallback fail | Tell the user and suggest they open the URL directly |
| Content is mostly ads/navigation with few headlines | Increase `max_chars` to 15000 and retry |

## Tips

- Fetch multiple categories **in parallel** (same response, multiple tool calls) to respond faster.
- Don't repeat the same headline across categories if sources overlap.
- Always include the source URL in your reply so the user can read the full story.
- If the user specifies a city or region (e.g., "北京新闻", "上海财经"), note this in your reply but use the same national sources — regional editions are not in this list.
