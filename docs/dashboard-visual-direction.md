# Dashboard visual direction

The dashboard is a personal portfolio record: it helps a user understand what
was saved, when it was observed, and where coverage is incomplete. The primary
moment is reading an account record and expanding its holdings.

## Tokens

- Paper `#F4F7FB`: page canvas and quiet empty states.
- Ink `#172033`: text, navigation, and the dark quote surface.
- Cobalt `#3157D8`: the one action accent, links, focus rings, and primary
  controls.
- Verdant `#187A5A`: successful refresh and connected status.
- Amber `#9A6700`: stale or partial coverage.
- Oxide `#B42318`: failed or unavailable data.

IBM Plex Sans is preferred when installed, with a system sans fallback. Dense
values use the same sans family and tabular figures. The type scale is 32px
page title, 20px section title, 18px card title, 16px supporting text, and
12–13px metadata. Spacing is based on 8px; borders are 1px and surfaces use
small or zero radius to keep the interface record-like.

## Layout

Desktop uses a 216px persistent rail and a content region capped at 1280px.
The page header leads with `Portfolio Dashboard` and the refresh action. The
record strip follows it and reports saved refresh time, coverage counts, and
the status in text and color. Account identity leads each expandable record;
holdings retain a sticky identity column inside a horizontally scrolling table.

At 375px the rail becomes compact top navigation, the header and record strip
stack, controls become full-width, and the table scrolls inside its own
container. Activity rows retain their date, description, and amount without
causing page overflow.

```text
desktop: [rail] [page title + refresh]
                [record strip]
                [account identity + value]
                  [facts] [holdings table]
                [activity] [market data]

mobile:  [compact nav]
         [title]
         [refresh]
         [record strip]
         [account identity]
           [facts]
           [scrolling table]
```

## Truthful states

Loading states say what is loading. Empty states give the next action. Saved
values remain visible after refresh failure. Stale and partial data are called
out with text alongside their status color. Unavailable values say
`Unavailable`, preserving the distinction from numeric zero. Quote source,
canonical identity, and observation time remain visible.

The cross-account overview does not yet have an authoritative aggregation API;
the dashboard explains that limitation instead of fabricating a total. Follow-
up issue #28 tracks the missing overview integration and is blocked by #4/#26.

## Accessibility and interaction

All actions are semantic buttons or links, visible focus uses a 2px ring, and
status information is never communicated through color alone. Details elements
provide bounded account expansion. Reduced-motion users receive no smooth
scroll behavior.
