# Frozen Prop Candidate Scaling and Survival Study v1

The complete preregistered frontier is reported. No signal changed and no payout-maximizing scale is promoted as optimal.

## Account and simulation

Primary rules: $100,000 nominal account, 3% daily loss limit, 6% static maximum loss, 8% evaluation target, 80% payout split, $500 initial/reset fee, and monthly funded payouts. A trailing-to-breakeven sensitivity is included.
Moving-block bootstrap: 5,000 paths, 21-session blocks, 252-session horizon. A breached account buys a fresh challenge on the next session; expected fees include those resets.

## Descriptive 5% annual-breach reference

This is a common-risk reference point, not a selected operating scale and not a promotion rule.

| Candidate | Largest tested scale at or below 5% breach | 12m breach | Expected net payout | Median net payout | P(any payout) | Median days to first payout |
|---|---:|---:|---:|---:|---:|---:|
| DM Optimized 63D/Daily | 0.25x | 2.9% | $5,216 | $5,183 | 89.8% | 138 |
| Canonical MRM | 0.30x | 2.9% | $-278 | $-500 | 14.3% | 205 |
| Frozen Vol-Scaled DM/MRM | 0.35x | 4.7% | $228 | $-500 | 30.7% | 190 |
| Fixed 50/50 DM/MRM | 0.30x | 2.3% | $-103 | $-500 | 20.6% | 200 |
| Canonical DM | 0.25x | 4.9% | $14 | $-500 | 22.1% | 189 |

## Primary static-rule frontier

| Candidate | Scale | Breach 1m | 3m | 6m | 12m | Expected payout | Median payout | Expected net | P05 net | P(any payout) | Median days to first payout | Median DD use | P95 DD use |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| DM Optimized 63D/Daily | 0.10x | 0.0% | 0.0% | 0.0% | 0.0% | $124 | $0 | $-376 | $-500 | 13.5% | 229 | 10% | 28% |
| DM Optimized 63D/Daily | 0.15x | 0.0% | 0.0% | 0.0% | 0.0% | $1,419 | $818 | $919 | $-500 | 59.8% | 195 | 23% | 48% |
| DM Optimized 63D/Daily | 0.20x | 0.0% | 0.0% | 0.2% | 0.6% | $3,461 | $3,268 | $2,958 | $-500 | 81.2% | 163 | 37% | 70% |
| DM Optimized 63D/Daily | 0.25x | 0.0% | 0.4% | 0.8% | 2.9% | $5,731 | $5,691 | $5,216 | $-500 | 89.8% | 138 | 50% | 91% |
| DM Optimized 63D/Daily | 0.30x | 0.0% | 1.2% | 2.7% | 9.2% | $8,065 | $8,073 | $7,518 | $-500 | 93.8% | 120 | 62% | 104% |
| DM Optimized 63D/Daily | 0.35x | 0.0% | 2.9% | 7.2% | 19.1% | $10,391 | $10,401 | $9,790 | $-248 | 95.5% | 107 | 75% | 107% |
| DM Optimized 63D/Daily | 0.40x | 8.4% | 24.7% | 44.3% | 69.7% | $11,252 | $11,074 | $10,185 | $-1,500 | 94.6% | 96 | 72% | 107% |
| DM Optimized 63D/Daily | 0.45x | 18.3% | 45.4% | 71.2% | 91.5% | $11,011 | $10,340 | $9,394 | $-2,000 | 94.1% | 93 | 79% | 111% |
| DM Optimized 63D/Daily | 0.50x | 26.4% | 58.9% | 83.7% | 97.3% | $11,484 | $10,610 | $9,325 | $-2,857 | 93.6% | 90 | 87% | 114% |
| DM Optimized 63D/Daily | 0.55x | 35.4% | 71.5% | 92.4% | 99.3% | $10,964 | $9,776 | $7,266 | $-5,000 | 90.7% | 91 | 91% | 115% |
| DM Optimized 63D/Daily | 0.60x | 44.9% | 82.8% | 97.3% | 99.9% | $9,582 | $8,234 | $5,295 | $-6,000 | 86.9% | 96 | 99% | 119% |
| DM Optimized 63D/Daily | 0.65x | 56.7% | 91.0% | 99.3% | 100.0% | $8,983 | $7,701 | $3,125 | $-8,000 | 84.6% | 98 | 101% | 124% |
| DM Optimized 63D/Daily | 0.70x | 66.8% | 95.8% | 99.8% | 100.0% | $7,060 | $5,540 | $-349 | $-10,500 | 74.6% | 105 | 105% | 126% |
| DM Optimized 63D/Daily | 0.75x | 74.6% | 98.3% | 100.0% | 100.0% | $6,696 | $5,111 | $-2,247 | $-12,508 | 68.3% | 110 | 103% | 128% |
| DM Optimized 63D/Daily | 0.80x | 76.7% | 98.9% | 100.0% | 100.0% | $5,813 | $3,902 | $-6,073 | $-16,500 | 60.9% | 117 | 107% | 132% |
| DM Optimized 63D/Daily | 0.85x | 84.3% | 99.6% | 100.0% | 100.0% | $5,147 | $2,118 | $-8,388 | $-18,000 | 54.0% | 119 | 111% | 140% |
| DM Optimized 63D/Daily | 0.90x | 85.6% | 99.6% | 100.0% | 100.0% | $5,569 | $2,235 | $-9,201 | $-19,500 | 53.8% | 116 | 109% | 141% |
| DM Optimized 63D/Daily | 0.95x | 87.0% | 99.7% | 100.0% | 100.0% | $5,589 | $1,384 | $-13,665 | $-25,473 | 52.0% | 117 | 102% | 126% |
| DM Optimized 63D/Daily | 1.00x | 87.7% | 99.7% | 100.0% | 100.0% | $5,055 | $0 | $-19,181 | $-32,500 | 44.2% | 125 | 105% | 126% |
| Canonical MRM | 0.10x | 0.0% | 0.0% | 0.0% | 0.0% | $0 | $0 | $-500 | $-500 | 0.0% | — | 8% | 29% |
| Canonical MRM | 0.15x | 0.0% | 0.0% | 0.0% | 0.0% | $0 | $0 | $-500 | $-500 | 0.0% | 248 | 12% | 44% |
| Canonical MRM | 0.20x | 0.0% | 0.0% | 0.0% | 0.3% | $12 | $0 | $-489 | $-500 | 1.4% | 228 | 17% | 59% |
| Canonical MRM | 0.25x | 0.0% | 0.0% | 0.3% | 1.1% | $80 | $0 | $-426 | $-500 | 6.5% | 218 | 22% | 73% |
| Canonical MRM | 0.30x | 0.0% | 0.2% | 1.0% | 2.9% | $236 | $0 | $-278 | $-500 | 14.3% | 205 | 30% | 88% |
| Canonical MRM | 0.35x | 0.0% | 0.7% | 2.4% | 5.9% | $499 | $0 | $-31 | $-1,000 | 24.0% | 194 | 39% | 101% |
| Canonical MRM | 0.40x | 0.0% | 1.4% | 4.2% | 9.4% | $857 | $0 | $308 | $-1,000 | 33.1% | 185 | 48% | 103% |
| Canonical MRM | 0.45x | 0.0% | 2.7% | 7.0% | 15.0% | $1,280 | $0 | $701 | $-1,000 | 40.9% | 176 | 58% | 105% |
| Canonical MRM | 0.50x | 0.5% | 4.5% | 10.2% | 21.0% | $1,765 | $0 | $1,150 | $-1,000 | 48.0% | 166 | 68% | 108% |
| Canonical MRM | 0.55x | 0.9% | 6.7% | 13.9% | 28.7% | $2,301 | $608 | $1,640 | $-1,000 | 54.4% | 158 | 77% | 111% |
| Canonical MRM | 0.60x | 3.1% | 12.6% | 24.4% | 45.5% | $2,854 | $1,408 | $2,069 | $-1,500 | 59.2% | 151 | 80% | 110% |
| Canonical MRM | 0.65x | 4.2% | 16.2% | 30.5% | 54.4% | $3,436 | $2,072 | $2,538 | $-1,500 | 63.7% | 145 | 87% | 112% |
| Canonical MRM | 0.70x | 7.1% | 22.2% | 39.8% | 66.8% | $3,824 | $2,407 | $2,591 | $-2,500 | 65.7% | 138 | 92% | 114% |
| Canonical MRM | 0.75x | 7.6% | 23.8% | 42.9% | 71.1% | $4,412 | $3,112 | $3,118 | $-2,500 | 69.2% | 132 | 100% | 118% |
| Canonical MRM | 0.80x | 8.2% | 25.9% | 46.3% | 74.8% | $5,079 | $3,879 | $3,709 | $-2,500 | 73.1% | 126 | 102% | 121% |
| Canonical MRM | 0.85x | 10.2% | 31.0% | 53.9% | 82.8% | $5,546 | $4,287 | $4,023 | $-2,500 | 74.5% | 120 | 104% | 124% |
| Canonical MRM | 0.90x | 13.7% | 39.3% | 64.1% | 89.7% | $5,652 | $4,327 | $3,859 | $-3,000 | 75.1% | 118 | 104% | 127% |
| Canonical MRM | 0.95x | 17.1% | 46.6% | 71.8% | 93.9% | $6,015 | $4,603 | $3,993 | $-3,384 | 77.1% | 114 | 105% | 129% |
| Canonical MRM | 1.00x | 22.1% | 55.2% | 80.3% | 96.7% | $6,142 | $4,761 | $3,655 | $-4,000 | 77.4% | 113 | 105% | 128% |
| Frozen Vol-Scaled DM/MRM | 0.10x | 0.0% | 0.0% | 0.0% | 0.0% | $0 | $0 | $-500 | $-500 | 0.0% | — | 8% | 27% |
| Frozen Vol-Scaled DM/MRM | 0.15x | 0.0% | 0.0% | 0.0% | 0.0% | $1 | $0 | $-499 | $-500 | 0.2% | 234 | 13% | 41% |
| Frozen Vol-Scaled DM/MRM | 0.20x | 0.0% | 0.0% | 0.0% | 0.1% | $29 | $0 | $-471 | $-500 | 2.8% | 223 | 17% | 55% |
| Frozen Vol-Scaled DM/MRM | 0.25x | 0.0% | 0.0% | 0.1% | 0.6% | $148 | $0 | $-355 | $-500 | 10.7% | 212 | 23% | 69% |
| Frozen Vol-Scaled DM/MRM | 0.30x | 0.0% | 0.1% | 0.6% | 2.1% | $391 | $0 | $-119 | $-500 | 20.5% | 199 | 31% | 84% |
| Frozen Vol-Scaled DM/MRM | 0.35x | 0.0% | 0.5% | 1.9% | 4.7% | $751 | $0 | $228 | $-500 | 30.7% | 190 | 40% | 98% |
| Frozen Vol-Scaled DM/MRM | 0.40x | 0.0% | 1.2% | 3.9% | 8.9% | $1,204 | $0 | $659 | $-1,000 | 39.8% | 180 | 49% | 103% |
| Frozen Vol-Scaled DM/MRM | 0.45x | 0.0% | 2.7% | 7.2% | 15.5% | $1,754 | $0 | $1,174 | $-1,000 | 47.7% | 171 | 59% | 106% |
| Frozen Vol-Scaled DM/MRM | 0.50x | 1.4% | 7.5% | 17.0% | 32.6% | $2,326 | $777 | $1,642 | $-1,000 | 54.8% | 162 | 66% | 107% |
| Frozen Vol-Scaled DM/MRM | 0.55x | 1.5% | 9.0% | 20.2% | 38.9% | $2,953 | $1,588 | $2,226 | $-1,000 | 60.9% | 154 | 75% | 110% |
| Frozen Vol-Scaled DM/MRM | 0.60x | 1.7% | 11.1% | 24.0% | 45.1% | $3,624 | $2,353 | $2,851 | $-1,000 | 66.2% | 145 | 84% | 113% |
| Frozen Vol-Scaled DM/MRM | 0.65x | 3.2% | 14.6% | 29.6% | 53.9% | $4,081 | $2,787 | $3,150 | $-1,500 | 68.2% | 140 | 93% | 117% |
| Frozen Vol-Scaled DM/MRM | 0.70x | 3.9% | 17.1% | 34.0% | 60.2% | $4,762 | $3,455 | $3,670 | $-2,000 | 71.8% | 133 | 100% | 120% |
| Frozen Vol-Scaled DM/MRM | 0.75x | 8.8% | 27.4% | 49.2% | 76.8% | $5,204 | $3,884 | $3,882 | $-2,500 | 74.1% | 128 | 101% | 125% |
| Frozen Vol-Scaled DM/MRM | 0.80x | 10.1% | 31.6% | 54.6% | 82.0% | $5,842 | $4,508 | $4,395 | $-2,500 | 76.7% | 122 | 102% | 128% |
| Frozen Vol-Scaled DM/MRM | 0.85x | 11.6% | 34.7% | 58.4% | 85.0% | $6,566 | $5,233 | $4,945 | $-2,562 | 79.0% | 117 | 103% | 126% |
| Frozen Vol-Scaled DM/MRM | 0.90x | 14.1% | 39.1% | 64.2% | 89.2% | $7,200 | $5,855 | $5,436 | $-3,000 | 81.4% | 113 | 104% | 130% |
| Frozen Vol-Scaled DM/MRM | 0.95x | 23.8% | 58.1% | 83.2% | 97.7% | $6,593 | $5,174 | $4,109 | $-4,000 | 78.8% | 111 | 105% | 130% |
| Frozen Vol-Scaled DM/MRM | 1.00x | 30.6% | 68.7% | 90.8% | 99.2% | $6,054 | $4,548 | $2,897 | $-5,000 | 76.8% | 112 | 105% | 133% |
| Fixed 50/50 DM/MRM | 0.10x | 0.0% | 0.0% | 0.0% | 0.0% | $0 | $0 | $-500 | $-500 | 0.0% | — | 8% | 28% |
| Fixed 50/50 DM/MRM | 0.15x | 0.0% | 0.0% | 0.0% | 0.0% | $1 | $0 | $-499 | $-500 | 0.2% | 226 | 12% | 42% |
| Fixed 50/50 DM/MRM | 0.20x | 0.0% | 0.0% | 0.0% | 0.1% | $31 | $0 | $-470 | $-500 | 3.2% | 223 | 17% | 55% |
| Fixed 50/50 DM/MRM | 0.25x | 0.0% | 0.0% | 0.1% | 0.6% | $160 | $0 | $-343 | $-500 | 10.8% | 212 | 23% | 70% |
| Fixed 50/50 DM/MRM | 0.30x | 0.0% | 0.1% | 0.8% | 2.3% | $409 | $0 | $-103 | $-500 | 20.6% | 200 | 31% | 87% |
| Fixed 50/50 DM/MRM | 0.35x | 0.0% | 0.3% | 2.2% | 5.5% | $776 | $0 | $249 | $-500 | 31.4% | 192 | 39% | 100% |
| Fixed 50/50 DM/MRM | 0.40x | 0.1% | 1.1% | 4.0% | 9.2% | $1,249 | $0 | $703 | $-1,000 | 40.1% | 180 | 48% | 103% |
| Fixed 50/50 DM/MRM | 0.45x | 0.1% | 2.5% | 6.5% | 14.4% | $1,794 | $0 | $1,220 | $-1,000 | 48.7% | 171 | 58% | 107% |
| Fixed 50/50 DM/MRM | 0.50x | 1.3% | 7.3% | 16.1% | 32.1% | $2,371 | $778 | $1,688 | $-1,000 | 54.4% | 161 | 64% | 107% |
| Fixed 50/50 DM/MRM | 0.55x | 1.4% | 8.9% | 18.8% | 37.8% | $3,019 | $1,605 | $2,298 | $-1,000 | 60.5% | 152 | 74% | 110% |
| Fixed 50/50 DM/MRM | 0.60x | 1.5% | 10.8% | 22.4% | 44.5% | $3,699 | $2,457 | $2,927 | $-1,000 | 65.2% | 146 | 83% | 113% |
| Fixed 50/50 DM/MRM | 0.65x | 3.4% | 14.8% | 29.1% | 53.7% | $4,172 | $2,888 | $3,238 | $-1,500 | 68.2% | 141 | 92% | 117% |
| Fixed 50/50 DM/MRM | 0.70x | 4.1% | 17.6% | 33.8% | 60.5% | $4,836 | $3,647 | $3,742 | $-2,000 | 72.4% | 132 | 100% | 120% |
| Fixed 50/50 DM/MRM | 0.75x | 8.1% | 27.3% | 48.6% | 77.1% | $5,196 | $3,870 | $3,874 | $-2,039 | 74.0% | 127 | 101% | 124% |
| Fixed 50/50 DM/MRM | 0.80x | 9.8% | 31.3% | 54.1% | 81.8% | $5,865 | $4,542 | $4,421 | $-2,500 | 76.7% | 121 | 102% | 129% |
| Fixed 50/50 DM/MRM | 0.85x | 11.0% | 33.5% | 57.4% | 84.9% | $6,576 | $5,289 | $4,960 | $-2,500 | 79.3% | 116 | 103% | 126% |
| Fixed 50/50 DM/MRM | 0.90x | 13.3% | 37.8% | 62.9% | 89.0% | $7,218 | $5,866 | $5,454 | $-2,890 | 81.7% | 112 | 104% | 131% |
| Fixed 50/50 DM/MRM | 0.95x | 23.3% | 58.1% | 82.7% | 97.7% | $6,569 | $4,965 | $4,046 | $-4,000 | 77.9% | 113 | 105% | 130% |
| Fixed 50/50 DM/MRM | 1.00x | 29.8% | 68.9% | 90.7% | 99.3% | $6,113 | $4,463 | $2,912 | $-5,000 | 76.6% | 112 | 105% | 132% |
| Canonical DM | 0.10x | 0.0% | 0.0% | 0.0% | 0.0% | $1 | $0 | $-499 | $-500 | 0.1% | 242 | 12% | 39% |
| Canonical DM | 0.15x | 0.0% | 0.0% | 0.0% | 0.1% | $34 | $0 | $-466 | $-500 | 3.2% | 218 | 18% | 58% |
| Canonical DM | 0.20x | 0.0% | 0.0% | 0.3% | 1.5% | $199 | $0 | $-308 | $-500 | 11.5% | 199 | 27% | 78% |
| Canonical DM | 0.25x | 0.0% | 0.3% | 1.7% | 4.9% | $538 | $0 | $14 | $-500 | 22.1% | 189 | 38% | 99% |
| Canonical DM | 0.30x | 0.0% | 0.9% | 4.1% | 10.1% | $1,027 | $0 | $475 | $-1,000 | 33.5% | 176 | 50% | 103% |
| Canonical DM | 0.35x | 0.1% | 2.6% | 7.8% | 18.0% | $1,657 | $0 | $1,061 | $-1,000 | 43.9% | 166 | 62% | 108% |
| Canonical DM | 0.40x | 1.1% | 5.9% | 13.2% | 28.2% | $2,349 | $284 | $1,691 | $-1,000 | 51.6% | 154 | 75% | 112% |
| Canonical DM | 0.45x | 2.5% | 11.4% | 23.4% | 45.0% | $3,139 | $1,399 | $2,356 | $-1,500 | 59.7% | 146 | 84% | 114% |
| Canonical DM | 0.50x | 3.2% | 14.5% | 28.7% | 54.2% | $3,961 | $2,387 | $3,100 | $-1,500 | 65.0% | 137 | 96% | 118% |
| Canonical DM | 0.55x | 5.7% | 23.1% | 41.8% | 70.3% | $4,392 | $2,683 | $3,352 | $-1,500 | 66.9% | 133 | 101% | 121% |
| Canonical DM | 0.60x | 9.5% | 31.8% | 54.6% | 82.1% | $4,799 | $3,154 | $3,373 | $-2,500 | 68.5% | 128 | 103% | 126% |
| Canonical DM | 0.65x | 15.1% | 42.8% | 68.1% | 91.1% | $5,199 | $3,389 | $3,241 | $-3,500 | 69.4% | 121 | 103% | 123% |
| Canonical DM | 0.70x | 19.7% | 50.5% | 75.8% | 94.9% | $5,844 | $3,980 | $3,406 | $-4,000 | 72.0% | 118 | 104% | 123% |
| Canonical DM | 0.75x | 23.7% | 58.4% | 83.4% | 97.4% | $6,247 | $4,406 | $3,394 | $-4,500 | 73.6% | 115 | 107% | 126% |
| Canonical DM | 0.80x | 30.9% | 68.6% | 90.4% | 99.0% | $6,501 | $4,569 | $3,150 | $-5,500 | 73.5% | 111 | 108% | 128% |
| Canonical DM | 0.85x | 37.7% | 76.9% | 94.7% | 99.6% | $6,415 | $4,316 | $2,281 | $-6,500 | 72.7% | 112 | 110% | 133% |
| Canonical DM | 0.90x | 42.5% | 82.5% | 97.0% | 99.9% | $6,565 | $4,378 | $1,757 | $-7,500 | 71.7% | 111 | 108% | 138% |
| Canonical DM | 0.95x | 49.0% | 87.6% | 98.8% | 100.0% | $6,789 | $4,535 | $1,036 | $-9,000 | 71.9% | 109 | 110% | 141% |
| Canonical DM | 1.00x | 51.5% | 89.3% | 99.1% | 100.0% | $6,956 | $4,665 | $76 | $-10,500 | 71.9% | 108 | 113% | 147% |

## Trailing-to-breakeven sensitivity

The high-then-low daily-bar ordering is deliberately conservative. This table uses the same descriptive 5% annual-breach reference.

| Candidate | Largest tested scale at or below 5% breach | 12m breach | Expected net payout | Median net payout | P(any payout) |
|---|---:|---:|---:|---:|---:|
| DM Optimized 63D/Daily | 0.20x | 3.7% | $2,949 | $2,766 | 81.4% |
| Canonical MRM | 0.25x | 3.4% | $-438 | $-500 | 6.5% |
| Frozen Vol-Scaled DM/MRM | 0.25x | 3.1% | $-368 | $-500 | 10.7% |
| Fixed 50/50 DM/MRM | 0.25x | 2.7% | $-354 | $-500 | 10.8% |
| Canonical DM | 0.20x | 5.0% | $-325 | $-500 | 11.5% |

## Interpretation limits

- Historical daily OHLC does not reveal the true account-equity path, whether different securities hit lows simultaneously, or whether a session high preceded its low. Static-rule results use a conservative simultaneous-low envelope; trailing results additionally use conservative high-then-low ordering. Both are approximate, not intraday-verified.
- DM Optimized 63D/Daily was selected from a historical search. Its one-year historical series is development evidence only; the live paper ledger is the authoritative forward test.
- The optimized candidate alone receives the aggregate observed-fill overlay. Those small-cap fills are not transferred to the Dow-based controls. Partial fills are approximated through observed mean exposure because order-level counterfactual replay is unavailable.
- Registered spreads and zero equity commission are charged by the underlying backtests. SEC/TAF pass-through fees are not represented. Challenge/reset fees and payout split are represented.
- Five years of Dow history and one year of optimized-candidate history are not long-history evidence. WRDS/CRSP remains the blocker for stronger regime and survivorship claims.
- The historical payout-maximizing scale is shown only as a diagnostic. Choosing it would violate the preregistered interpretation rule.

## Artifacts

`frontier.csv` contains every candidate, scale, and drawdown rule. `results.json` contains the same frontier, source provenance, execution calibration, and preregistration hash.
