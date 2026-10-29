"""Setting the demo company up behaves as NAZAR does: a second site, groups, a site with history."""

from tests.test_demo_server import demo, people, signed_in

NEW_SITE = {
    "name": "ЖК Олмазор", "address": "Ташкент, Чиланзар", "latitude": 41.2856, "longitude": 69.2034,
    "radius_meters": 100, "shift_start": "08:00", "shift_end": "17:00", "work_schedule_mode": "custom", "working_days": [1, 2, 3, 4, 5],
}


async def test_a_second_site_is_added_to_the_first_one_not_instead_of_it():
    state = demo()
    rustam = people(state, "Рустам Юлдашев")
    home = state.world.people[rustam].site_id
    async with signed_in(state) as client:
        site = (await client.post("/objects", json=NEW_SITE)).json()["data"]
        await client.post(f"/objects/{site['id']}/assignments", json={"employee_ids": [rustam]})

        there = (await client.get(f"/objects/{site['id']}/assignments")).json()["data"]["assignments"]
        at_home = (await client.get(f"/objects/{home}/assignments")).json()["data"]["assignments"]
        card = next(item for item in (await client.get("/employees")).json()["data"] if item["id"] == rustam)

        await client.post(f"/objects/{site['id']}/assignments", json={"employee_ids": []})
        after = (await client.get(f"/objects/{site['id']}/assignments")).json()["data"]["assignments"]

    assert [item["employee_id"] for item in there] == [rustam] and rustam in {item["employee_id"] for item in at_home}
    assert card["object_count"] == 2 and site["working_days"] == [1, 2, 3, 4, 5]
    assert after == [] and state.world.people[rustam].site_id == home


async def test_a_group_counts_its_members_who_work_at_the_site():
    state = demo()
    rustam, bobur = people(state, "Рустам Юлдашев"), people(state, "Бобур Каримов")
    async with signed_in(state) as client:
        site = (await client.post("/objects", json=NEW_SITE)).json()["data"]["id"]
        group = (await client.post("/departments", json={"name": "Бригада Рустама"})).json()["data"]["id"]
        twice = await client.post("/departments", json={"name": "бригада рустама"})
        await client.post(f"/objects/{site}/groups", json={
            "department_id": group, "schedule_mode": "custom", "shift_start": "09:00", "shift_end": "18:00", "working_days": [1, 2, 3],
        })
        added = (await client.post("/departments/bulk-add", json={"employee_ids": [rustam, bobur], "department_ids": [group]})).json()
        await client.post(f"/objects/{site}/assignments", json={"employee_ids": [rustam]})
        groups = (await client.get(f"/objects/{site}/groups")).json()["data"]["groups"]
        card = next(item for item in (await client.get("/employees")).json()["data"] if item["id"] == bobur)

    assert twice.status_code == 409 and added["data"]["changed_count"] == 2
    assert groups[0]["employee_count"] == 1 and groups[0]["shift_start"] == "09:00" and groups[0]["schedule_type"] == "weekly"
    assert card["departments"] == [{"id": group, "name": "Бригада Рустама", "color": "blue"}]


async def test_a_site_with_check_ins_is_not_deleted_a_new_one_is():
    state = demo()
    old = next(iter(state.world.sites))
    async with signed_in(state) as client:
        refused = await client.delete(f"/objects/{old}")
        site = (await client.post("/objects", json=NEW_SITE)).json()["data"]["id"]
        deleted = await client.delete(f"/objects/{site}")
        gone = await client.get(f"/objects/{site}/groups")

    assert refused.status_code == 409 and old in state.world.sites
    assert deleted.status_code == 200 and gone.status_code == 404


async def test_a_night_shift_previews_into_the_next_morning():
    state = demo()
    site = next(iter(state.world.sites))
    async with signed_in(state) as client:
        shown = (await client.post(f"/objects/{site}/schedule-preview", json={
            "shift_start": "20:00", "shift_end": "08:00", "working_days": [3], "start_date": "2026-10-28", "days": 2,
        })).json()["data"]

    wednesday, thursday = shown["days"]
    assert shown["shift_duration_minutes"] == 720 and shown["schedule_label"] == "weekly:3"
    assert wednesday["is_working"] and wednesday["planned_end_at"].startswith("2026-10-29T03:00")
    assert not thursday["is_working"] and thursday["planned_minutes"] == 0
