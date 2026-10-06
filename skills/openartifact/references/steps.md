# Build steps

Part of the OpenArtifact skill; `SKILL.md` covers the format. Build steps apply to decks only.

A deck page can reveal its content in steps, like Keynote builds. Put `data-step="N"` on any element, in the markdown or inside a component, and it stays hidden until the slide reaches step N. Add `data-step-end="M"` to hide it again after step M. The slide's step count is the highest step mentioned plus one; a slide with no `data-step` attributes has a single step.

The next/previous keys, the wheel and any `data-nav` control in the page component step through a page's builds before moving to the next page, and a slide entered backwards opens on its last step. Shift+Right and Shift+Left jump a whole slide, skipping the builds, and land on the target's first step.

```html
<ul>
  <li>Always visible</li>
  <li data-step="1">Appears on the first press</li>
  <li data-step="2">Appears on the second press</li>
</ul>
```

Mutually exclusive frames (a diagram that changes rather than grows) are ranges. Stack them with `position: absolute` or a one-cell grid so they occupy the same space:

```html
<div style="position: relative; height: 20rem;">
  <img data-step="0" data-step-end="0" src="assets/before.svg" style="position: absolute; inset: 0;">
  <img data-step="1" data-step-end="1" src="assets/during.svg" style="position: absolute; inset: 0;">
  <img data-step="2" src="assets/after.svg" style="position: absolute; inset: 0;">
</div>
```

Hidden elements keep their layout (`visibility: hidden`, so build-up lists don't reflow) and fade in when revealed. The runtime only writes `data-step-state` (`pending` | `active` | `done`) on stepped elements and `data-step` on the slide; the look is plain CSS you can override from `styles.css`, for example to dim finished elements instead of hiding them:

```css
[data-step-state='done'] { visibility: visible; opacity: 0.35; }
```

PDF output shows every page at its final step.
