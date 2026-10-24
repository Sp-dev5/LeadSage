# Data notes

Where the text in these files comes from.

- `real_leads.csv`: ten real companies. Each description is the company's own public description, copied by hand. Size and location come from their public profiles.
- `test_leads.csv`: 25 real companies. Twenty have no typed description and are scored from their own websites. Five have one, because their sites could not be read:
  - Duolingo, Infosys and Tata Consultancy Services: the opening paragraph of their Wikipedia articles ([Duolingo](https://en.wikipedia.org/wiki/Duolingo), [Infosys](https://en.wikipedia.org/wiki/Infosys), [Tata Consultancy Services](https://en.wikipedia.org/wiki/Tata_Consultancy_Services)). Wikipedia text is licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); these excerpts keep that license.
  - Vymo and Sprinto: our own short summary of web search results about them.
- `labelled_leads.csv`, `unseen_leads.csv`, `unseen_leads_2.csv`: made-up companies written for testing. No real company or person is described.
- `outreach.json`: the example emails are written for a made-up company.
- Websites the app reads are saved in `data/pages/` and the lead store in `data/leadsage.db`. Both are git-ignored and never committed. The app deletes a lead's rows and saved pages when you delete the lead.
