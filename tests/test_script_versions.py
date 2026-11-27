from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.script_writer import brief_for, generate_json, has_loop, hotel_plan, required, script_preview, validate_beats
from videoport.scripts import ScriptStudio, add_version, story_version, upgrade_project
from videoport.storyboard_images import prompt_text


class ScriptVersionsTests(TestCase):
    def test_short_command_keeps_chat_brief_and_excludes_advice(self):
        project = {"title": "Ресепшн", "messages": [
            {"role": "user", "text": "Гость устал. Девушка шепчет и открывает журнал. Камера статичная."},
            {"role": "assistant", "text": "Старый зацикленный ответ"},
            {"role": "user", "mode": "discussion", "text": "Что такое глубина резкости?"},
        ]}
        result = brief_for(project, "Написать сценарий")
        self.assertIn("открывает журнал", result)
        self.assertNotIn("Написать сценарий", result)
        self.assertNotIn("глубина резкости", result)

    def test_invalid_model_answer_retries_without_creating_a_version(self):
        answers = iter([
            '{"answer":"Извините, но вы не указали тему."}',
            '{"answer":"' + ("Автор шепчет и касается ручки на столе. " * 35) + '"}',
            '{"answer":"Я приготовила для вас ключ. Устраивайтесь удобно."}',
        ])
        result = generate_json(lambda _: next(answers), "Ответ", {}, required({"answer": {"type": "string"}}),
                               lambda payload: payload["answer"], 120)
        self.assertTrue(result.startswith("Я приготовила"))
        self.assertTrue(has_loop("Она посмотрела в камеру и снова закрыла глаза. " * 20))
        self.assertIn("Я приготовила", script_preview("Заголовок\nРеплика · «Я приготовила ключ.»"))

    def test_legacy_project_upgrades_with_stable_board_and_numbering(self):
        old = {"id": "a" * 32, "messages": [{"role": "assistant", "text": "Сценарий №1"}],
               "latest": "Сценарий №1", "shots": [{"id": "b" * 32, "image": ""}]}
        self.assertEqual(upgrade_project(old)["boards"][0]["id"], "a" * 32)
        self.assertEqual(add_version(old, "Сценарий №2", 1)["number"], 2)
        self.assertEqual(story_version(old, 1)["text"], "Сценарий №1")
        self.assertEqual(old["boards"][0]["version"], 1)

    def test_manual_blocks_carry_exact_text_and_sort_by_position(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            studio = ScriptStudio(root, root)
            key = studio.create({"title": "Ресепшен", "mode": "short", "platform": "shorts", "seconds": 20})["id"]
            project = studio.read(key)
            add_version(project, "Первый. Второй.")
            board = {"shots": [], "mode": "manual", "version": 1}
            project["boards"].append(board)
            studio.add_shot(project, board, {"start": 8, "end": 15, "instruction": "Крупно"})
            studio.add_shot(project, board, {"start": 0, "end": 7})
            self.assertEqual([shot["source"] for shot in board["shots"]], ["Первый.", "Второй."])
            self.assertEqual(board["shots"][1]["instruction"], "Крупно")

    def test_shared_visual_continuity_survives_frame_changes(self):
        first = prompt_text("sketch", "adult receptionist in navy uniform", "hands on a key")
        second = prompt_text("sketch", "adult receptionist in navy uniform", "face beside a lamp")
        self.assertIn("adult receptionist in navy uniform", first)
        self.assertIn("adult receptionist in navy uniform", second)
        self.assertIn("pencil", first)
        self.assertNotEqual(first, second)

    def test_hotel_story_keeps_check_in_as_part_of_the_plot(self):
        plan = hotel_plan(6)
        self.assertEqual(len(set(plan)), 6)
        self.assertTrue(any("бронирование" in stage for stage in plan))
        self.assertTrue(any("ключ" in stage for stage in plan))
        beats = [
            {"camera_action": "Она садится на стул", "spoken_line": "Дыши спокойно"},
            {"camera_action": "Она закрывает глаза", "spoken_line": "Послушай тишину"},
        ]
        with self.assertRaises(ValueError):
            validate_beats({"beats": beats}, 2, set(), hotel=True)
