/**
 * Compliance footer — CLAUDE.md §2.5: rendered on EVERY public route via the
 * root layout. CI renders each route and asserts the marker below is present
 * (scripts/check-compliance-footer.mjs), so removing this fails the build.
 *
 * Copy note: §2.4 bans profit-guarantee language (CI-linted in any casing),
 * so the disclaimer is phrased without any of the banned tokens.
 */
export const COMPLIANCE_MARKER = "compliance-footer";

export default function ComplianceFooter() {
  return (
    <footer className="site" data-compliance={COMPLIANCE_MARKER}>
      <div className="container">
        <div>
          18+. Informational and educational content only — never betting
          advice and never a promise of profit. Outcomes are always uncertain.
          Bet responsibly. This site never places bets and never handles
          wagers.
        </div>
        <div>
          Need help with gambling?{" "}
          <a href="https://www.begambleaware.org" rel="noopener noreferrer">
            BeGambleAware
          </a>{" "}
          ·{" "}
          <a href="https://www.gamblingtherapy.org" rel="noopener noreferrer">
            Gambling Therapy
          </a>{" "}
          ·{" "}
          <a href="https://jocresponsabil.ro" rel="noopener noreferrer">
            Joc Responsabil (RO)
          </a>
        </div>
      </div>
    </footer>
  );
}
