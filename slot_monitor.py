import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List, Callable
from dataclasses import dataclass
from enum import Enum

import nodriver as uc

logger = logging.getLogger(__name__)

# Create debug directory for HTML snapshots
DEBUG_HTML_DIR = os.path.join(os.path.dirname(__file__), "debug_html")
os.makedirs(DEBUG_HTML_DIR, exist_ok=True)


class SlotStatus(Enum):
    SEARCHING = "searching"
    FOUND = "found"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class CapturedSlot:
    """Result of successful slot capture"""
    date: date
    time: str
    center: str
    captured_at: datetime


# ============================================================================
# CORE DETECTION JS — Single source of truth for "is this date bookable?"
#
# A date cell is bookable if ALL of:
#   - NOT a weeklyOff cell (Sundays / permanent holidays)
# AND ANY of:
#   1. <button> has explicit `enabled` attribute
#   2. <button> does NOT have `disabled` attribute (and .disabled is false)
#   3. <td> has `is-enabled` class
#
# This covers every known combination:
#   td.is-disabled + button[disabled]       → NOT bookable (current booked state)
#   td.is-disabled.weeklyOff + button[disabled] → NOT bookable (weekly off)
#   td.is-enabled  + button[enabled]        → BOOKABLE (explicit available)
#   td.is-disabled + button[enabled]        → BOOKABLE (button overrides parent)
#   td (no is-disabled) + button (no disabled) → BOOKABLE (standard available)
# ============================================================================

AVAILABLE_DATE_JS = """
(() => {
    const available = [];
    const cells = document.querySelectorAll('td.datepicker__day');
    cells.forEach(cell => {
        // Always skip weekly off (Sundays / permanent holidays)
        if (cell.classList.contains('weeklyOff')) return;

        const btn = cell.querySelector('button.datepicker__button');
        if (!btn) return;

        // Determine bookability: trust button state first, then td class
        const hasEnabled    = btn.hasAttribute('enabled');
        const notDisabled   = !btn.hasAttribute('disabled') && !btn.disabled;
        const tdEnabled     = cell.classList.contains('is-enabled');
        const tdNotDisabled = !cell.classList.contains('is-disabled');

        // Bookable if button says enabled, OR button not disabled, OR td says enabled
        const isBookable = hasEnabled || notDisabled || tdEnabled;

        if (isBookable) {
            const day = parseInt(btn.textContent.trim(), 10);
            if (!isNaN(day) && day > 0) {
                available.push({
                    day: day,
                    classes: cell.className,
                    btnAttrs: {
                        enabled: hasEnabled,
                        disabled: btn.hasAttribute('disabled'),
                        tdEnabled: tdEnabled,
                        tdDisabled: cell.classList.contains('is-disabled')
                    }
                });
            }
        }
    });
    return available;
})()
"""

# Quick boolean version (avoids building the full array)
HAS_ANY_AVAILABLE_JS = """
(() => {
    const cells = document.querySelectorAll('td.datepicker__day');
    for (const cell of cells) {
        if (cell.classList.contains('weeklyOff')) continue;
        const btn = cell.querySelector('button.datepicker__button');
        if (!btn) continue;
        const hasEnabled  = btn.hasAttribute('enabled');
        const notDisabled = !btn.hasAttribute('disabled') && !btn.disabled;
        const tdEnabled   = cell.classList.contains('is-enabled');
        if (hasEnabled || notDisabled || tdEnabled) return true;
    }
    return false;
})()
"""

# Count-only version
COUNT_AVAILABLE_JS = """
(() => {
    let count = 0;
    const cells = document.querySelectorAll('td.datepicker__day');
    cells.forEach(cell => {
        if (cell.classList.contains('weeklyOff')) return;
        const btn = cell.querySelector('button.datepicker__button');
        if (!btn) return;
        const hasEnabled  = btn.hasAttribute('enabled');
        const notDisabled = !btn.hasAttribute('disabled') && !btn.disabled;
        const tdEnabled   = cell.classList.contains('is-enabled');
        if (hasEnabled || notDisabled || tdEnabled) count++;
    });
    return count;
})()
"""


class SlotHunter:

    SELECTORS = {
        # Calendar structure
        "calendar": "sb-datepicker",
        "calendar_wrapper": "div.datepicker__wrapper",
        "calendar_table": "div.datepicker__calendar-wrapper",

        # Date cells (used as fallback only — prefer JS methods)
        "available_date": "td.datepicker__day:not(.is-disabled)",
        "disabled_date": "td.datepicker__day.is-disabled",
        "date_button": "button.datepicker__button:not([disabled])",
        "all_day_cells": "td.datepicker__day",

        # Navigation arrows
        "next_month": "button.navigation__button.is-next",
        "prev_month": "button.navigation__button.is-previous",

        # Month label
        "month_label": "div.navigation__title-wrapper, div.navigation__title",
        "month_span": "div.navigation__title-wrapper span:first-child, div.navigation__title span:first-child",
        "year_span": "div.navigation__title-wrapper span:last-child, div.navigation__title span:last-child",

        # Time slots
        "no_slots_message": "div.noSlotTimeDiv",
        "time_slot_container": "div.time-slots, div.slot-times, div[class*='timeSlot']",
        "available_time": "button.time-slot:not([disabled]), div.time-slot:not(.disabled), button[class*='time']:not([disabled])",

        # Appointment type radio
        "appointment_type_normal": "input[name='appointmentType'][value='Normal']",
        "appointment_type_premium": "input[name='appointmentType'][value='Premium']",

        # Center dropdown
        "center_dropdown": "button[name='selectedVsc']",
        "center_dropdown_menu": "button[name='selectedVsc'] + ul.dropdown-menu",

        # Confirm/Book button
        "confirm_btn": "button[translate*='book'], button.btn-submit, button[type='submit']:not([disabled])",

        # Popups
        "popup_close": ".modal button.close, .modal .btn-close, button.btn-close",
    }

    def __init__(
        self,
        page: uc.Tab,
        target_center: str = "Islamabad",
        poll_interval: float = 2.0,
        max_poll_duration: int = 3600,
        date_range: Tuple[date, date] = None,
        on_slot_found: Callable = None,
        proxy_manager=None,
        browser_engine=None,
    ):
        self.page = page
        self.target_center = target_center
        self.poll_interval = poll_interval
        self.max_poll_duration = max_poll_duration
        self.date_range = date_range
        self.on_slot_found = on_slot_found
        self.proxy_manager = proxy_manager
        self.browser_engine = browser_engine

        # State
        self.status = SlotStatus.SEARCHING
        self.poll_count = 0
        self.current_month_index = 0
        self.max_months = 5  # Jan → May based on observation
        self._stop_flag = False
        self._consecutive_empty_polls = 0
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ========================================================================
    # HTML Debug Snapshots
    # ========================================================================

    async def _log_html_snapshot(self, event_name: str, selector: str = None):
        if not self.page:
            return
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{self._session_id}_{timestamp}_{event_name}.html"
            filepath = os.path.join(DEBUG_HTML_DIR, filename)

            if selector:
                html = await self.page.evaluate(f'''
                    (() => {{
                        const el = document.querySelector("{selector}");
                        return el ? el.outerHTML : "ELEMENT NOT FOUND: {selector}";
                    }})()
                ''')
            else:
                html = await self.page.evaluate("document.documentElement.outerHTML")

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"<!-- Event: {event_name} -->\n")
                f.write(f"<!-- URL: {self.page.url} -->\n")
                f.write(f"<!-- Time: {datetime.now().isoformat()} -->\n")
                f.write(f"<!-- Poll: {self.poll_count} -->\n")
                if selector:
                    f.write(f"<!-- Selector: {selector} -->\n")
                f.write(html)

            logger.info(f"[HTML] {event_name}: {filepath}")

        except Exception as e:
            logger.warning(f"Failed to capture HTML snapshot: {e}")

    # ========================================================================
    # Center Selection
    # ========================================================================

    async def _select_center(self) -> bool:
        """Select the target QVC center from dropdown"""
        try:
            logger.info(f"Selecting center: {self.target_center}")

            current_result = await self.page.evaluate("""
                (() => {
                    const btn = document.querySelector('button[name="selectedVsc"]');
                    return btn ? btn.textContent.trim() : '';
                })()
            """)

            current_selection = current_result
            if isinstance(current_result, dict) and 'value' in current_result:
                current_selection = current_result['value']

            if current_selection and self.target_center.lower() in str(current_selection).lower():
                logger.info(f"Center '{self.target_center}' already selected")
                return True

            dropdown = await self.page.select(self.SELECTORS["center_dropdown"], timeout=5)
            if not dropdown:
                logger.warning("Center dropdown not found")
                return False

            await dropdown.click()
            await asyncio.sleep(0.5)

            xpath = (
                f"//button[@name='selectedVsc']"
                f"/following-sibling::ul//li/a[contains(text(), '{self.target_center}')]"
            )
            option = await self.page.find(xpath, timeout=3)

            if option:
                await option.click()
                logger.info(f"Selected center: {self.target_center}")
                await asyncio.sleep(1)
                return True

            # Fallback: CSS
            css_option = "button[name='selectedVsc'] + ul.dropdown-menu li a"
            options = await self.page.select_all(css_option)
            for opt in options:
                text = opt.text or ""
                if self.target_center.lower() in text.lower():
                    await opt.click()
                    logger.info(f"Selected center: {self.target_center} (fallback)")
                    await asyncio.sleep(1)
                    return True

            logger.error(f"Center option '{self.target_center}' not found")
            return False

        except Exception as e:
            logger.error(f"Failed to select center: {e}")
            return False

    # ========================================================================
    # Calendar Navigation
    # ========================================================================

    async def _get_current_month_year(self) -> Tuple[int, int]:
        """Get currently displayed month and year from calendar header"""
        try:
            result = await self.page.evaluate("""
                (() => {
                    const titleWrapper = document.querySelector('div.navigation__title-wrapper') ||
                                         document.querySelector('div.navigation__title');
                    if (titleWrapper) {
                        const spans = titleWrapper.querySelectorAll('span');
                        if (spans.length >= 2) {
                            return {
                                month: spans[0].textContent.trim(),
                                year: spans[spans.length - 1].textContent.trim()
                            };
                        }
                        return { text: titleWrapper.textContent.trim() };
                    }
                    return null;
                })()
            """)

            # Convert nodriver's list format to dict if needed
            if isinstance(result, list):
                result_dict = {}
                for item in result:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        val_obj = item[1]
                        if isinstance(val_obj, dict) and 'value' in val_obj:
                            result_dict[key] = val_obj['value']
                        else:
                            result_dict[key] = val_obj
                result = result_dict

            months_map = {
                "january": 1, "jan": 1,
                "february": 2, "feb": 2,
                "march": 3, "mar": 3,
                "april": 4, "apr": 4,
                "may": 5,
                "june": 6, "jun": 6,
                "july": 7, "jul": 7,
                "august": 8, "aug": 8,
                "september": 9, "sep": 9,
                "october": 10, "oct": 10,
                "november": 11, "nov": 11,
                "december": 12, "dec": 12,
            }

            if result:
                if 'month' in result and 'year' in result:
                    month = months_map.get(str(result['month']).lower(), 1)
                    year = int(result['year'])
                    logger.debug(f"Current calendar: {result['month']} {result['year']} -> {month}/{year}")
                    return (month, year)
                elif 'text' in result:
                    text = str(result['text']).replace('\xa0', ' ').strip()
                    parts = text.split()
                    if len(parts) >= 2:
                        month = months_map.get(parts[0].lower(), 1)
                        year = int(parts[-1])
                        return (month, year)

        except Exception as e:
            logger.debug(f"Could not parse month label: {e}")

        now = datetime.now()
        return (now.month, now.year)

    async def _go_to_next_month(self) -> bool:
        """Click next month button. Returns False if disabled/unavailable."""
        try:
            next_btn = await self.page.select(self.SELECTORS["next_month"], timeout=2)
            if not next_btn:
                logger.debug("Next month button not found")
                return False

            is_disabled = (
                next_btn.attrs.get('disabled') is not None
                or 'disabled' in str(next_btn.attrs)
            )

            if is_disabled:
                logger.debug("Next month button is disabled (reached max month)")
                return False

            await next_btn.click()
            # Wait long enough for getvscappointmentdates API to respond and
            # Angular to re-render the calendar before we read DOM state.
            await asyncio.sleep(10.0)
            self.current_month_index += 1

            month, year = await self._get_current_month_year()
            logger.debug(f"Navigated to: {month}/{year}")
            return True

        except Exception as e:
            logger.debug(f"Failed to navigate to next month: {e}")
            return False

    async def _go_to_first_month(self) -> bool:
        """Reset calendar to first available month"""
        try:
            clicks = 0
            for _ in range(self.max_months):
                prev_btn = await self.page.select(self.SELECTORS["prev_month"], timeout=1)
                if not prev_btn:
                    break

                is_disabled = (
                    prev_btn.attrs.get('disabled') is not None
                    or 'disabled' in str(prev_btn.attrs)
                )
                if is_disabled:
                    break

                await prev_btn.click()
                await asyncio.sleep(0.3)
                clicks += 1

            self.current_month_index = 0
            if clicks > 0:
                logger.debug(f"Reset calendar: clicked prev {clicks} times")
            return True

        except Exception as e:
            logger.debug(f"Failed to reset to first month: {e}")
            return False

    async def _navigate_to_month(self, target_month: int, target_year: int) -> bool:
        """
        Navigate the calendar to the given month/year by clicking next/prev.
        Tries up to 24 steps in either direction. Returns True on success.
        """
        try:
            for _ in range(24):
                cur_month, cur_year = await self._get_current_month_year()
                if cur_month == target_month and cur_year == target_year:
                    return True

                cur_total  = cur_year  * 12 + cur_month
                tgt_total  = target_year * 12 + target_month

                if tgt_total > cur_total:
                    btn = await self.page.select(self.SELECTORS["next_month"], timeout=2)
                    if not btn:
                        break
                    is_disabled = (
                        btn.attrs.get('disabled') is not None
                        or 'disabled' in str(btn.attrs)
                    )
                    if is_disabled:
                        break
                    await btn.click()
                    # Give the API time to respond before the next navigation click
                    await asyncio.sleep(10.0)
                else:
                    btn = await self.page.select(self.SELECTORS["prev_month"], timeout=2)
                    if not btn:
                        break
                    is_disabled = (
                        btn.attrs.get('disabled') is not None
                        or 'disabled' in str(btn.attrs)
                    )
                    if is_disabled:
                        break
                    await btn.click()
                    await asyncio.sleep(10.0)

            cur_month, cur_year = await self._get_current_month_year()
            success = (cur_month == target_month and cur_year == target_year)
            if not success:
                logger.debug(
                    f"_navigate_to_month: landed on {cur_month}/{cur_year}, "
                    f"wanted {target_month}/{target_year}"
                )
            return success

        except Exception as e:
            logger.debug(f"_navigate_to_month failed: {e}")
            return False

    # ========================================================================
    # DATE DETECTION — All via JS to avoid stale nodriver element refs
    # ========================================================================

    async def _has_any_available_date_in_month(self) -> bool:
        """Quick boolean: does current month have ANY bookable date?"""
        try:
            result = await self.page.evaluate(HAS_ANY_AVAILABLE_JS)
            if isinstance(result, bool):
                return result
            if isinstance(result, dict) and 'value' in result:
                return result['value']
            return bool(result)
        except:
            return False

    async def _get_available_date_count(self) -> int:
        """Count of bookable dates in current month view"""
        try:
            result = await self.page.evaluate(COUNT_AVAILABLE_JS)
            if isinstance(result, int):
                return result
            if isinstance(result, dict) and 'value' in result:
                return result['value']
            return int(result) if result else 0
        except:
            return 0

    async def _scan_current_month(self) -> list:
        """
        Return list of dicts [{day, classes, btnAttrs}, ...] for all bookable dates.
        Entirely JS-based — no stale element issues.
        """
        try:
            month, year = await self._get_current_month_year()
            result = await self.page.evaluate(AVAILABLE_DATE_JS)

            # Normalise nodriver response
            dates = result if isinstance(result, list) else []

            if dates:
                days = [d.get('day') if isinstance(d, dict) else d for d in dates]
                logger.info(f"✓ Found {len(dates)} bookable date(s) in {month}/{year}: {days}")
                for d in dates:
                    if isinstance(d, dict):
                        attrs = d.get('btnAttrs', {})
                        logger.debug(
                            f"  Day {d.get('day')}: td_class='{d.get('classes')}' "
                            f"btn_enabled={attrs.get('enabled')} "
                            f"btn_disabled={attrs.get('disabled')} "
                            f"td_enabled={attrs.get('tdEnabled')} "
                            f"td_disabled={attrs.get('tdDisabled')}"
                        )
            else:
                logger.debug(f"No bookable dates in {month}/{year}")

            return dates

        except Exception as e:
            logger.debug(f"Error scanning month: {e}")
            return []

    # ========================================================================
    # Date Range Filter
    # ========================================================================

    async def _filter_dates_in_range(self, available_dates: list) -> list:
        """Filter scanned dates to only those within the configured date_range."""
        if not self.date_range:
            return available_dates

        start, end = self.date_range
        month, year = await self._get_current_month_year()
        filtered = []

        for d in available_dates:
            try:
                day_num = d.get('day') if isinstance(d, dict) else int(d)
                element_date = date(year, month, day_num)
                if start <= element_date <= end:
                    filtered.append(d)
            except (ValueError, TypeError):
                # Accept if we can't verify
                filtered.append(d)

        return filtered

    # ========================================================================
    # Click Actions — All via JS
    # ========================================================================

    async def _click_date_js(self, day_number: int) -> bool:
        """Click a bookable date by day number entirely via JS (no stale refs)."""
        try:
            result = await self.page.evaluate(f"""
                (() => {{
                    const cells = document.querySelectorAll('td.datepicker__day');
                    for (const cell of cells) {{
                        if (cell.classList.contains('weeklyOff')) continue;

                        const btn = cell.querySelector('button.datepicker__button');
                        if (!btn) continue;

                        const hasEnabled  = btn.hasAttribute('enabled');
                        const notDisabled = !btn.hasAttribute('disabled') && !btn.disabled;
                        const tdEnabled   = cell.classList.contains('is-enabled');

                        if (!(hasEnabled || notDisabled || tdEnabled)) continue;

                        const day = parseInt(btn.textContent.trim(), 10);
                        if (day === {day_number}) {{
                            btn.click();
                            return {{
                                clicked: true,
                                day: day,
                                method: hasEnabled  ? 'btn-enabled-attr'
                                       : tdEnabled  ? 'td-is-enabled'
                                       :              'btn-not-disabled'
                            }};
                        }}
                    }}
                    return {{ clicked: false }};
                }})()
            """)

            # Normalise nodriver response
            if isinstance(result, list):
                result_dict = {}
                for item in result:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        val_obj = item[1]
                        result_dict[key] = val_obj['value'] if isinstance(val_obj, dict) and 'value' in val_obj else val_obj
                result = result_dict

            if isinstance(result, dict) and result.get('clicked'):
                method = result.get('method', 'unknown')
                logger.info(f"Clicked day {day_number} (detected via: {method})")
                await asyncio.sleep(1.5)  # Wait for time slots to load
                return True

            logger.warning(f"Day {day_number} not found or not bookable")
            return False

        except Exception as e:
            logger.error(f"JS click on day {day_number} failed: {e}")
            return False

    async def _select_time_slot(self) -> Optional[str]:
        """Find and click the first available time slot after a date has been clicked."""
        try:
            await asyncio.sleep(1.5)

            # Check for "No slots" message
            no_slots_result = await self.page.evaluate("""
                (() => {
                    const noSlot = document.querySelector('div.noSlotTimeDiv');
                    return noSlot ? noSlot.textContent.trim() : null;
                })()
            """)

            no_slots_text = no_slots_result
            if isinstance(no_slots_result, dict) and 'value' in no_slots_result:
                no_slots_text = no_slots_result['value']

            if no_slots_text and "no slot" in str(no_slots_text).lower():
                logger.warning(f"No time slots available: {no_slots_text}")
                return None

            # Select Normal appointment type if present
            try:
                normal_radio = await self.page.select(
                    self.SELECTORS["appointment_type_normal"], timeout=2
                )
                if normal_radio:
                    await normal_radio.click()
                    logger.debug("Selected 'Normal' appointment type")
                    await asyncio.sleep(0.5)
            except:
                pass

            # Find and click time slot via JS
            time_result = await self.page.evaluate("""
                () => {
                    const selectors = [
                        'button.time-slot:not([disabled])',
                        'div.time-slot:not(.disabled):not(.booked)',
                        'button[class*="time"]:not([disabled])',
                        '.slot-item:not(.disabled) button',
                        'a.time-slot:not(.disabled)'
                    ];

                    for (const selector of selectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            const text = el.textContent.trim();
                            if (/\\d{1,2}[:\\s]?\\d{0,2}\\s*(AM|PM)?/i.test(text) ||
                                text.includes(':') ||
                                /^\\d{1,2}\\s*(AM|PM)$/i.test(text)) {
                                el.click();
                                return { success: true, text: text };
                            }
                        }
                    }

                    // Fallback: any button with HH:MM pattern
                    const allButtons = document.querySelectorAll('button:not([disabled])');
                    for (const btn of allButtons) {
                        const text = btn.textContent.trim();
                        if (/\\d{1,2}:\\d{2}/.test(text)) {
                            btn.click();
                            return { success: true, text: text };
                        }
                    }

                    return { success: false, text: null };
                }
            """)

            # Normalise nodriver list response
            if isinstance(time_result, list):
                result_dict = {}
                for item in time_result:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        val_obj = item[1]
                        result_dict[key] = val_obj['value'] if isinstance(val_obj, dict) and 'value' in val_obj else val_obj
                time_result = result_dict

            if isinstance(time_result, dict) and time_result.get('success'):
                slot_text = time_result.get('text', 'Unknown')
                logger.info(f"[OK] Selected time slot: {slot_text}")
                await asyncio.sleep(0.5)
                return slot_text

            logger.warning("No time slots found after date selection")
            return None

        except Exception as e:
            logger.error(f"Failed to select time slot: {e}")
            return None

    # ========================================================================
    # Popup Handling
    # ========================================================================

    async def _close_any_popup(self) -> bool:
        """Close any notification/error popup that might appear"""
        try:
            close_btn = await self.page.select(self.SELECTORS["popup_close"], timeout=1)
            if close_btn:
                await close_btn.click()
                await asyncio.sleep(0.5)
                return True
        except:
            pass
        return False

    # ========================================================================
    # Calendar Refresh
    # ========================================================================

    async def _refresh_calendar(self):
        """Refresh calendar data without full page reload."""
        try:
            await self._go_to_first_month()
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.debug(f"Calendar refresh failed: {e}")

    # ========================================================================
    # MAIN HUNTING LOOP
    # ========================================================================

    async def hunt(self) -> Optional[CapturedSlot]:
        """
        Main hunting loop. Continuously polls calendar until slot found or timeout.

        Returns:
            CapturedSlot if successful, None if timeout/error
        """
        logger.info("=" * 60)
        logger.info("SLOT HUNTER STARTED")
        logger.info(f"  Center       : {self.target_center}")
        logger.info(f"  Poll interval: {self.poll_interval}s")
        logger.info(f"  Max duration : {self.max_poll_duration}s ({self.max_poll_duration / 60:.0f} min)")
        if self.date_range:
            logger.info(f"  Date range   : {self.date_range[0]} to {self.date_range[1]}")
        logger.info("  Detection covers: is-enabled, enabled attr, not-disabled, weeklyOff guard")
        logger.info("=" * 60)

        start_time = asyncio.get_event_loop().time()

        # Initial setup
        await self._close_any_popup()
        await self._select_center()
        await asyncio.sleep(1)

        # Select Normal appointment type once at start
        try:
            normal_radio = await self.page.select(
                self.SELECTORS["appointment_type_normal"], timeout=2
            )
            if normal_radio:
                await normal_radio.click()
                logger.info("Selected 'Normal' appointment type")
        except:
            pass

        while not self._stop_flag:
            elapsed = asyncio.get_event_loop().time() - start_time

            if elapsed > self.max_poll_duration:
                logger.warning(
                    f"Slot hunting timeout after {elapsed:.0f}s ({elapsed / 60:.1f} min)"
                )
                self.status = SlotStatus.TIMEOUT
                return None

            self.poll_count += 1
            remaining = self.max_poll_duration - elapsed

            if self.poll_count <= 5 or self.poll_count % 10 == 0:
                range_str = (
                    f"{self.date_range[0]} to {self.date_range[1]}"
                    if self.date_range else "all months"
                )
                logger.info(
                    f"[Poll #{self.poll_count}] Scanning {range_str} "
                    f"({remaining:.0f}s / {remaining / 60:.1f}min remaining)"
                )
                if self.poll_count == 1 or self.poll_count % 10 == 0:
                    await self._log_html_snapshot(
                        f"poll_{self.poll_count}_calendar",
                        self.SELECTORS["calendar"],
                    )

            try:
                # Navigate to the start of our date range (not the absolute first month)
                if self.date_range:
                    start_month, start_year = self.date_range[0].month, self.date_range[0].year
                else:
                    today = date.today()
                    start_month, start_year = today.month, today.year

                await self._navigate_to_month(start_month, start_year)
                await asyncio.sleep(10.0)  # Wait for API response after navigation

                # Compute how many months to scan to cover the full date range
                if self.date_range:
                    end = self.date_range[1]
                    months_to_scan = (end.year - start_year) * 12 + (end.month - start_month) + 1
                    months_to_scan = max(1, min(months_to_scan, self.max_months))
                else:
                    months_to_scan = self.max_months

                # Scan through months within the date range
                months_scanned = 0
                for month_idx in range(months_to_scan):
                    month, year = await self._get_current_month_year()

                    # ---- FAST CHECK ----
                    available_count = await self._get_available_date_count()

                    # ---- CALENDAR STATE LOG ----
                    try:
                        cell_summary = await self.page.evaluate("""
                            (() => {
                                const cells = document.querySelectorAll('td.datepicker__day');
                                let disabled = 0, enabled = 0, weekly = 0;
                                cells.forEach(cell => {
                                    if (cell.classList.contains('weeklyOff')) { weekly++; return; }
                                    const btn = cell.querySelector('button.datepicker__button');
                                    if (!btn) return;
                                    const isEnabled = btn.hasAttribute('enabled') ||
                                                      (!btn.hasAttribute('disabled') && !btn.disabled) ||
                                                      cell.classList.contains('is-enabled');
                                    if (isEnabled) enabled++; else disabled++;
                                });
                                return { total: cells.length, enabled, disabled, weekly };
                            })()
                        """)
                        # Normalise nodriver list response to dict
                        if isinstance(cell_summary, list):
                            tmp = {}
                            for item in cell_summary:
                                if isinstance(item, list) and len(item) == 2:
                                    k = item[0]
                                    v = item[1]
                                    tmp[k] = v['value'] if isinstance(v, dict) and 'value' in v else v
                            cell_summary = tmp
                        if isinstance(cell_summary, dict):
                            logger.info(
                                f"[Calendar {month}/{year}] "
                                f"Total={cell_summary.get('total',0)} | "
                                f"Available={cell_summary.get('enabled',0)} | "
                                f"Disabled={cell_summary.get('disabled',0)} | "
                                f"WeeklyOff={cell_summary.get('weekly',0)}"
                            )
                    except Exception:
                        pass

                    if available_count > 0:
                        # Reset failure counter
                        self._consecutive_empty_polls = 0
                        if self.proxy_manager:
                            await self.proxy_manager.report_success()

                        logger.info("!" * 60)
                        logger.info(
                            f"🎉 AVAILABLE DATES DETECTED: {available_count} in {month}/{year}!"
                        )
                        logger.info("!" * 60)

                        # Capture HTML snapshot
                        await self._log_html_snapshot(f"slots_found_{month}_{year}")

                        # Full scan with details
                        available_dates = await self._scan_current_month()

                        # Filter to date range
                        in_range = await self._filter_dates_in_range(available_dates)

                        found_days = []
                        for d in in_range:
                            if isinstance(d, dict):
                                day = d.get('day', 0)
                            else:
                                day = int(d) if d else 0
                            if day > 0:
                                found_days.append(day)

                        if not found_days and self.date_range:
                            # Dates exist but outside our range — keep scanning
                            logger.info(
                                f"  Dates found but outside range "
                                f"{self.date_range[0]}–{self.date_range[1]}, skipping..."
                            )
                            months_scanned += 1
                            if month_idx < months_to_scan - 1:
                                if not await self._go_to_next_month():
                                    break
                                await asyncio.sleep(8.0)
                            continue

                        # Build result
                        try:
                            first_day = found_days[0] if found_days else 1
                            slot_date = date(year, month, first_day)
                        except:
                            slot_date = date.today()

                        result = CapturedSlot(
                            date=slot_date,
                            time="DETECTED (not clicked)",
                            center=self.target_center,
                            captured_at=datetime.now(),
                        )

                        self.status = SlotStatus.FOUND

                        logger.info("=" * 60)
                        logger.info(f"🎯 SLOT DETECTED: {result.date}")
                        logger.info(f"   Available days in {month}/{year}: {found_days}")
                        logger.info(f"   Center: {result.center}")
                        logger.info(f"   Detected at: {result.captured_at}")
                        logger.info(f"   Total polls: {self.poll_count}")
                        logger.info(f"   Time elapsed: {elapsed:.0f}s")

                        # Log detection method for each day
                        for d in in_range:
                            if isinstance(d, dict):
                                attrs = d.get('btnAttrs', {})
                                if attrs.get('enabled'):
                                    method = "button[enabled] attribute"
                                elif attrs.get('tdEnabled'):
                                    method = "td.is-enabled class"
                                elif attrs.get('tdDisabled'):
                                    method = "button NOT disabled (td still is-disabled)"
                                else:
                                    method = "standard (no disabled)"
                                logger.info(f"   Day {d.get('day')}: {method}")

                        logger.info("=" * 60)
                        logger.info("✓ Detection complete — NOT clicking/booking (detection mode)")

                        if self.on_slot_found:
                            await self.on_slot_found(result)

                        return result

                    else:
                        months_scanned += 1
                        if month_idx < months_to_scan - 1:
                            if not await self._go_to_next_month():
                                break
                            await asyncio.sleep(8.0)

                # All months scanned — nothing found
                self._consecutive_empty_polls += 1

                if self._consecutive_empty_polls >= 50 and self.proxy_manager:
                    logger.warning("Extended empty results — raising soft block error")
                    raise Exception("Soft blocked suspected")

                if self.poll_count <= 5 or self.poll_count % 30 == 0:
                    logger.debug(
                        f"No slots in {months_scanned} months. "
                        f"Waiting {self.poll_interval}s..."
                    )

                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error during poll #{self.poll_count}: {e}")
                await self._log_html_snapshot(f"poll_{self.poll_count}_error")

                if "blocked" in str(e).lower():
                    raise e

                await asyncio.sleep(self.poll_interval)

        self.status = SlotStatus.ERROR
        return None

    def stop(self):
        """Stop the hunting loop"""
        logger.info("Stop signal received")
        self._stop_flag = True

    async def hunt_with_callback(
        self,
        on_poll: Callable = None,
        on_found: Callable = None,
        on_timeout: Callable = None,
    ) -> Optional[CapturedSlot]:
        self.on_slot_found = on_found

        result = await self.hunt()

        if result and on_found:
            await on_found(result)
        elif self.status == SlotStatus.TIMEOUT and on_timeout:
            await on_timeout()

        return result