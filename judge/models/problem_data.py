import errno
import os

from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.utils.problem_data import ProblemDataStorage

__all__ = ['problem_data_storage', 'problem_directory_file', 'ProblemData', 'ProblemTestCase', 'CHECKERS']

problem_data_storage = ProblemDataStorage()


def _problem_directory_file(code, filename):
    return os.path.join(code, os.path.basename(filename))


def problem_directory_file(data, filename):
    return _problem_directory_file(data.problem.code, filename)


CHECKERS = (
    ('standard', _('Standard')),
    ('floats', _('Floats')),
    ('floatsabs', _('Floats (absolute)')),
    ('floatsrel', _('Floats (relative)')),
    ('rstripped', _('Non-trailing spaces')),
    ('sorted', _('Sorted')),
    ('identical', _('Byte identical')),
    ('linecount', _('Line-by-line')),
)


class ProblemData(models.Model):
    problem = models.OneToOneField('Problem', verbose_name=_('problem'), related_name='data_files',
                                   on_delete=models.CASCADE)
    zipfile = models.FileField(verbose_name=_('data zip file'), storage=problem_data_storage, null=True, blank=True,
                               upload_to=problem_directory_file)
    generator = models.FileField(verbose_name=_('generator file'), storage=problem_data_storage, null=True, blank=True,
                                 upload_to=problem_directory_file)
    output_prefix = models.IntegerField(verbose_name=_('output prefix length'), blank=True, null=True)
    output_limit = models.IntegerField(verbose_name=_('output limit length'), blank=True, null=True)
    feedback = models.TextField(verbose_name=_('init.yml generation feedback'), blank=True)
    checker = models.CharField(max_length=10, verbose_name=_('checker'), choices=CHECKERS, blank=True)
    unicode = models.BooleanField(verbose_name=_('enable unicode'), null=True, blank=True)
    nobigmath = models.BooleanField(verbose_name=_('disable bigInteger / bigDecimal'), null=True, blank=True)
    checker_args = models.TextField(verbose_name=_('checker arguments'), blank=True,
                                    help_text=_('Checker arguments as a JSON object.'))
    interactor = models.FileField(verbose_name=_('interactor'), storage=problem_data_storage, null=True, blank=True,
                                  upload_to=problem_directory_file,
                                  help_text=_('Makes the problem interactive: the program that talks with each '
                                              'submission, in C++, C or Python. Leave it empty for a normal '
                                              'problem.'))
    # db_default keeps the column fillable by code that predates it, so rolling
    # back the code alone never breaks the creation of new data rows.
    interactor_feedback = models.BooleanField(verbose_name=_('show interactor messages'), default=False,
                                              db_default=False,
                                              help_text=_('Show contestants what the interactor writes to standard '
                                                          'error.'))

    # Files whose previous version is removed from disk once replaced or cleared.
    REPLACED_FILES = ('zipfile', 'interactor')

    __original_files = None

    def __init__(self, *args, **kwargs):
        super(ProblemData, self).__init__(*args, **kwargs)
        self.__original_files = {field: getattr(self, field).name for field in self.REPLACED_FILES}

    def save(self, *args, **kwargs):
        result = super(ProblemData, self).save(*args, **kwargs)
        # Old files go by name, and only once the new ones are saved. This used to
        # call FieldFile.delete first, which also blanks the field on this very
        # instance: a zip uploaded over another one was never stored, and the
        # problem was left with test cases and no archive. A new upload that ends
        # up with the old name has already replaced the file, so it stays.
        for field, original in self.__original_files.items():
            current = getattr(self, field).name
            if original and original != current:
                problem_data_storage.delete(original)
            self.__original_files[field] = current
        return result

    def has_yml(self):
        return problem_data_storage.exists('%s/init.yml' % self.problem.code)

    def _update_code(self, original, new):
        try:
            problem_data_storage.rename(original, new)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        if self.zipfile:
            self.zipfile.name = _problem_directory_file(new, self.zipfile.name)
        if self.generator:
            self.generator.name = _problem_directory_file(new, self.generator.name)
        if self.interactor:
            self.interactor.name = _problem_directory_file(new, self.interactor.name)
        # The directory moved as a whole; there is no old copy left to remove.
        self.__original_files = {field: getattr(self, field).name for field in self.REPLACED_FILES}
        self.save()
    _update_code.alters_data = True


class ProblemTestCase(models.Model):
    dataset = models.ForeignKey('Problem', verbose_name=_('problem data set'), related_name='cases',
                                on_delete=models.CASCADE)
    order = models.IntegerField(verbose_name=_('case position'))
    type = models.CharField(max_length=1, verbose_name=_('case type'),
                            choices=(('C', _('Normal case')),
                                     ('S', _('Batch start')),
                                     ('E', _('Batch end'))),
                            default='C')
    input_file = models.CharField(max_length=100, verbose_name=_('input file name'), blank=True)
    output_file = models.CharField(max_length=100, verbose_name=_('output file name'), blank=True)
    generator_args = models.TextField(verbose_name=_('generator arguments'), blank=True)
    points = models.IntegerField(verbose_name=_('point value'), blank=True, null=True)
    is_pretest = models.BooleanField(verbose_name=_('case is pretest?'))
    output_prefix = models.IntegerField(verbose_name=_('output prefix length'), blank=True, null=True)
    output_limit = models.IntegerField(verbose_name=_('output limit length'), blank=True, null=True)
    checker = models.CharField(max_length=10, verbose_name=_('checker'), choices=CHECKERS, blank=True)
    checker_args = models.TextField(verbose_name=_('checker arguments'), blank=True,
                                    help_text=_('checker arguments as a JSON object'))
    batch_dependencies = models.TextField(verbose_name=_('batch dependencies'), blank=True,
                                          help_text=_('batch dependencies as a comma-separated list of integers'))
