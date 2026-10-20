# Maintaining Lumina's public discoverability

Reviewed October 19, 2026. This is a maintainer checklist for keeping search-facing descriptions accurate. It is not a measurement of search rankings or AI recommendations.

## Describe one identifiable project

Use **Lumina**, **https://luminamedia.app/** and **https://github.com/shiftedx/lumina** consistently. Describe Lumina as an open-source, self-hosted household media server that combines local movies, TV and music with YouTube, Twitch and yt-dlp saving. Explain Jellyfin client compatibility separately from Jellyfin Server compatibility.

The public README, repository About field, website and release descriptions should agree on those facts. Do not describe every Jellyfin app as compatible: Lumina does not serve jellyfin-web, run Jellyfin server plugins or accept Plex client connections. Keep version-specific evidence in the operator guide and release notes.

## Help readers answer concrete questions

| Reader's question | Public source to maintain |
| --- | --- |
| What is Lumina, and how do I install it? | [README](../README.md) |
| Should I consider Lumina alongside Plex or Jellyfin? | [Media-server comparison](lumina-vs-plex-vs-jellyfin.md) |
| Can my Infuse, Roku or Android TV client connect? | [Client compatibility guide](jellyfin-clients.md) |
| Can I bring over Jellyfin progress? | [Migration explanation](jellyfin-clients.md#can-i-move-from-jellyfin-to-lumina) and [operator procedure](../docker/README.md#moving-over-from-jellyfin) |
| Is it free, local, and suitable for my hardware? | [FAQ](faq.md) and [operator guide](../docker/README.md) |

Review these pages when a release changes supported clients, online sources, authentication or migration. Change review dates only after checking the claims. Link competitor claims to their own documentation; avoid unverified negative claims, invented ratings and universal performance rankings.

## Website implementation and follow-through

The marketing website source is maintained separately in the household's internal GitLab. The October 19, 2026 update publishes the [comparison](https://luminamedia.app/docs/lumina-vs-plex-vs-jellyfin/), [client guide](https://luminamedia.app/docs/jellyfin-clients/) and [FAQ](https://luminamedia.app/docs/faq/) as server-delivered HTML. They are linked from navigation, included in the sitemap and indexed by the site's local documentation search. The homepage now describes the media server and includes SoftwareApplication/SoftwareSourceCode structured data; documentation pages include WebPage and breadcrumb data.

Public fetches verified the generated pages, canonicals and sitemap against the deployed build. The existing allow-all robots rule remains. This establishes fetchability from the checking client, not search indexing or access from every search crawler. Before this change, the fetched homepage did not contain SoftwareApplication JSON-LD.

Maintain these contracts when updating that site's source:

1. Keep the comparison, client and FAQ material available as readable HTML linked from the relevant documentation navigation. Use one canonical website URL for each page and include it in the sitemap. Repository Markdown alone does not publish new website routes.
2. Use descriptive page titles and summaries, such as “Lumina: self-hosted media server for local media, YouTube and Twitch” and “Lumina vs Plex vs Jellyfin.” Keep substantive answers visible without JavaScript.
3. Keep SoftwareApplication structured data accurate, matching visible content and linking the official repository. Do not invent reviews, aggregate ratings or compatibility claims. Validate the rendered result; a standalone JSON file in a repo would not add page markup.
4. Verify Googlebot and search-provider crawler access through the actual hosting layer. A permissive robots file does not prove that a CDN or firewall allows a crawler. Training-crawler preferences are a separate decision from search access.
5. Verify ownership in search webmaster tools and submit the sitemap. This requires the website owner's account and is not completed by editing this repository.

Google's [AI features guidance](https://developers.google.com/search/docs/appearance/ai-features) emphasizes ordinary search eligibility, accessible text, internal links and structured data that matches the page. There is no special markup required for AI features. An optional `llms.txt` navigation file is not a substitute for public pages, indexing or evidence of citation improvements.

## Measure the result

No Search Console, Bing Webmaster or AI citation analytics were accessed for this change. Organic impressions, positions, clicks, referral traffic and AI recommendation frequency are **unmeasured**. Do not assign a fabricated readiness score or claim a ranking lift.

After the documentation is merged and any website pages are published, capture a baseline for branded searches and relevant questions about self-hosted media servers, Plex alternatives, Jellyfin alternatives, Infuse and YouTube libraries. In the owner's webmaster accounts, inspect indexing and query impressions after recrawling. Leading indicators are discovered/indexed pages and relevant impressions; downstream outcomes are qualified visits, installations and accurately sourced recommendations.

For a small manual AI-search check, save each exact question, system/date, whether web search was enabled, the answer and cited URLs. Repeat under comparable conditions. One answer is not a visibility measurement. If pages remain unindexed, diagnose discovery and access first; if indexed pages receive no relevant impressions, revisit whether they answer a real need. Repeated factual misstatements about clients or migration are a reason to improve the documentation even if traffic rises.
