# Teatro dell’Opera di Roma – Teatro Costanzi (opera only)

A dynamic `.ics` subscription created from official production pages at <https://www.operaroma.it/>.

**Includes:** staged operas only at **Teatro Costanzi**, Rome. Each performance is a separate event with local start time, venue, URL, conductor and published cast information when available.

**Excludes:** ballet, concerts, 'Opere in Concerto' (concert-form operas), previews ('Anteprima giovani'), and productions at the Teatro Nazionale, Teatro Valle, Terme di Caracalla or Circo Massimo.

**Duration:** nominal 3 hours, explicitly marked as an estimate; the published start time is used. The source may change its HTML at any time; review GitHub Actions logs for warnings.

## GitHub setup

1. Create a **public** repository called `opera-roma-calendar` (or any name).
2. Upload `update_calendar.py`, `requirements.txt`, and `README.md` to the repository root.
3. Create a workflow file in GitHub: **Actions > New workflow > set up a workflow yourself**. Name the file `.github/workflows/update.yml`, and paste the supplied `update.yml` content. If a default workflow already appears, replace its entire content.
4. Choose **Actions > Update Opera di Roma calendar > Run workflow**.
5. When the run is green, check that `opera-roma-costanzi.ics` exists in the repository root and is non-empty. If a script error occurs, check the action log before subscribing.
6. Subscribe (not import) in Apple Calendar using:

   `https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/opera-roma-calendar/main/opera-roma-costanzi.ics`

Replace `YOUR_GITHUB_USERNAME` and the repository name as appropriate. For macOS Calendar: **File > New Calendar Subscription**; set desired auto-refresh interval.

### Further seasons

The script re-discovers productions from the official site every day, and the output contains all presently discovered future performance dates. It is not intentionally restricted to the 2026/27 season. The website can change structure, however, so successful collection beyond that season is **not guaranteed** without maintenance.

### Cast caveat

The website often lists multiple performers with date-specific substitutions in the same cast section. The calendar includes this source text and warns that a particular performance's cast should be verified. It does **not** assert a specific singer is performing on a date unless those details can be reliably parsed.
