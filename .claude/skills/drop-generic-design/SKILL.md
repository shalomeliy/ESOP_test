---
name: drop-generic-design
description: Remove generic AI-generated design patterns and replace them with product-specific hierarchy, language, evidence, states, and actions. Use for frontend concepts, implementations, and reviews.
---

# Drop the generic design

Treat “looks polished” and “helps the user decide” as different quality bars.

## Detect generic output

Flag:

- Generic gradient hero treatments unrelated to the product.
- Repeated equal cards with vague labels such as Fast, Smart, Secure, or Powerful.
- Invented scores, percentages, testimonials, or precision.
- Generic CTAs such as Get started when a specific next action exists.
- Static success-only mockups with no failure, ownership, or recovery path.
- Decoration substituting for information hierarchy.
- UI copy that could belong to any product.

## Replace it with packaged expertise

1. Name the user and the decision they are making.
2. Put the decisive evidence first.
3. Show state, reason, owner, timing, and next action when relevant.
4. Use domain language found in the product and brief.
5. Expose uncertainty instead of inventing confidence.
6. Reuse the existing visual system.
7. Verify accessibility and responsive behavior.

Use `/design-demo` in the running application as a deterministic before/after teaching example.
